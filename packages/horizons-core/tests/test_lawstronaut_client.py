"""Tests for the Lawstronaut HTTP client.

The client is driven through ``httpx.MockTransport`` against hand-rolled
JSON fixtures under ``tests/fixtures/lawstronaut/``. The clock is
injected so refresh-timing tests are deterministic without sleeping or
patching ``time``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from horizons_core.core.lawstronaut import (
    Credentials,
    Jurisdiction,
    LawstronautAuthError,
    LawstronautClient,
    LawstronautClientError,
    LawstronautTransientError,
    Portal,
)

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).parent / "fixtures" / "lawstronaut"

API_BASE = "https://api.example.invalid/v2"
AUTH_BASE = "https://auth.example.invalid/auth/login"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class CallRecorder:
    """Records every request the client sends through the mock transport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._handlers: list[Callable[[httpx.Request], httpx.Response]] = []

    def add(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handlers.append(handler)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for handler in self._handlers:
            resp = handler(request)
            if resp is not None:
                return resp
        raise AssertionError(f"no handler matched: {request.method} {request.url}")


def static_handler(
    *,
    method: str,
    url_substr: str,
    status: int,
    body: dict[str, Any] | str,
) -> Callable[[httpx.Request], httpx.Response | None]:
    """Returns a handler that responds to the first matching request only.

    A list of these inside a ``CallRecorder`` becomes a deterministic
    request-sequence model.
    """
    used = {"v": False}

    def handler(req: httpx.Request) -> httpx.Response | None:
        if used["v"]:
            return None
        if req.method != method:
            return None
        if url_substr not in str(req.url):
            return None
        used["v"] = True
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return handler


def login_handler(
    *,
    fixture: str = "login_response_nested.json",
    status: int = 200,
) -> Callable[[httpx.Request], httpx.Response | None]:
    body = load_fixture(fixture)
    return static_handler(method="POST", url_substr=AUTH_BASE, status=status, body=body)


def credentials() -> Credentials:
    return Credentials(email="demo@example.invalid", password="hunter2")


def make_client(
    recorder: CallRecorder,
    *,
    clock: Callable[[], float] | None = None,
    refresh_buffer_s: float = 300.0,
    stamina_attempts: int = 4,
    stamina_wait_initial: float = 0.001,
    stamina_wait_max: float = 0.01,
) -> LawstronautClient:
    transport = httpx.MockTransport(recorder)
    return LawstronautClient(
        credentials=credentials(),
        base_url=API_BASE,
        auth_url=AUTH_BASE,
        clock=clock if clock is not None else _real_monotonic,
        refresh_buffer_s=refresh_buffer_s,
        transport=transport,
        stamina_attempts=stamina_attempts,
        stamina_wait_initial=stamina_wait_initial,
        stamina_wait_max=stamina_wait_max,
    )


def _real_monotonic() -> float:
    # Indirection so tests that don't care about time still get a real clock,
    # without importing `time` at module top (TC003 — type-only imports trap).
    import time

    return time.monotonic()


# --- login + auth -----------------------------------------------------------


async def test_login_extracts_nested_bearer_and_expiry() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/jurisdictions",
            status=200,
            body=load_fixture("jurisdictions_response.json"),
        )
    )
    async with make_client(rec) as client:
        await client.list_jurisdictions()
    auth_req = rec.requests[0]
    assert auth_req.method == "POST"
    assert str(auth_req.url) == AUTH_BASE
    api_req = rec.requests[1]
    assert api_req.headers["authorization"] == "Bearer BEARER_TOKEN_A"


async def test_login_response_missing_token_raises() -> None:
    rec = CallRecorder()
    rec.add(
        static_handler(
            method="POST",
            url_substr=AUTH_BASE,
            status=200,
            body={"data": {"token": {"expires_in": 1800}}},
        )
    )
    with pytest.raises(LawstronautAuthError):
        async with make_client(rec) as client:
            await client.list_jurisdictions()


async def test_login_401_raises_auth_error_and_does_not_retry() -> None:
    rec = CallRecorder()
    rec.add(
        static_handler(
            method="POST",
            url_substr=AUTH_BASE,
            status=401,
            body={"message": "invalid credentials"},
        )
    )
    with pytest.raises(LawstronautAuthError):
        async with make_client(rec) as client:
            await client.list_jurisdictions()
    assert len(rec.requests) == 1


# --- pre-emptive refresh ----------------------------------------------------


async def test_no_refresh_before_buffer_boundary() -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    rec = CallRecorder()
    # Two logins worth of fixtures; the second should never fire.
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/jurisdictions",
            status=200,
            body=load_fixture("jurisdictions_response.json"),
        )
    )
    rec.add(login_handler(fixture="login_response_nested_b.json"))
    rec.add(
        static_handler(
            method="GET",
            url_substr="/jurisdictions",
            status=200,
            body=load_fixture("jurisdictions_response.json"),
        )
    )
    async with make_client(rec, clock=clock, refresh_buffer_s=300.0) as client:
        await client.list_jurisdictions()  # @ t=0, no refresh (just logged in)
        # 24:59 elapsed → 1499 s. Token expires at 1800. Buffer 300 → refresh @ 1500.
        now[0] = 1499.0
        await client.list_jurisdictions()
    posts = [r for r in rec.requests if r.method == "POST"]
    assert len(posts) == 1, "expected one login at t=0, got an unexpected refresh"


async def test_refresh_fires_at_buffer_boundary() -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/jurisdictions",
            status=200,
            body=load_fixture("jurisdictions_response.json"),
        )
    )
    rec.add(login_handler(fixture="login_response_nested_b.json"))
    rec.add(
        static_handler(
            method="GET",
            url_substr="/jurisdictions",
            status=200,
            body=load_fixture("jurisdictions_response.json"),
        )
    )
    async with make_client(rec, clock=clock, refresh_buffer_s=300.0) as client:
        await client.list_jurisdictions()
        now[0] = 1501.0  # past the 25-minute mark
        await client.list_jurisdictions()
    posts = [r for r in rec.requests if r.method == "POST"]
    assert len(posts) == 2, "expected one login at t=0 and one refresh at t=1501"
    second_api_call = [r for r in rec.requests if r.method == "GET"][1]
    assert second_api_call.headers["authorization"] == "Bearer BEARER_TOKEN_B"


# --- contention under concurrent callers ------------------------------------


async def test_concurrent_refresh_under_contention_runs_exactly_once() -> None:
    now = [1501.0]  # already past refresh buffer on first call

    def clock() -> float:
        return now[0]

    rec = CallRecorder()
    rec.add(login_handler())  # implicit login on first use
    for _ in range(10):
        rec.add(
            static_handler(
                method="GET",
                url_substr="/jurisdictions",
                status=200,
                body=load_fixture("jurisdictions_response.json"),
            )
        )
    # After the implicit login (which sets expires_at = 1501 + 1800), all 10
    # GETs find the token fresh and need no refresh. To force the "refresh
    # under contention" scenario, do an initial login first then push the
    # clock forward.

    async with make_client(rec, clock=clock, refresh_buffer_s=300.0) as client:
        await client.list_jurisdictions()  # initial login + 1 GET
        # Token expires at 1501 + 1800 = 3301. Buffer 300 → refresh @ 3001.
        now[0] = 3002.0
        # Inject one extra login fixture for the contention refresh:
        rec.add(login_handler(fixture="login_response_nested_b.json"))
        # ...and N more GETs to satisfy the concurrent callers
        for _ in range(20):
            rec.add(
                static_handler(
                    method="GET",
                    url_substr="/jurisdictions",
                    status=200,
                    body=load_fixture("jurisdictions_response.json"),
                )
            )
        await asyncio.gather(*(client.list_jurisdictions() for _ in range(8)))

    posts = [r for r in rec.requests if r.method == "POST"]
    assert len(posts) == 2, f"expected exactly 2 logins (initial + 1 refresh), got {len(posts)}"


# --- stamina retry ----------------------------------------------------------


async def test_503_then_200_succeeds() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(static_handler(method="GET", url_substr="/jurisdictions", status=503, body="upstream"))
    rec.add(
        static_handler(
            method="GET",
            url_substr="/jurisdictions",
            status=200,
            body=load_fixture("jurisdictions_response.json"),
        )
    )
    async with make_client(rec) as client:
        jurisdictions = await client.list_jurisdictions()
    assert any(j.iso == "IE" for j in jurisdictions)
    gets = [r for r in rec.requests if r.method == "GET"]
    assert len(gets) == 2, "expected one retry after 503"


async def test_persistent_5xx_raises_transient_after_attempts() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    for _ in range(8):
        rec.add(
            static_handler(method="GET", url_substr="/jurisdictions", status=503, body="upstream")
        )
    async with make_client(rec, stamina_attempts=4) as client:
        with pytest.raises(LawstronautTransientError):
            await client.list_jurisdictions()
    gets = [r for r in rec.requests if r.method == "GET"]
    assert len(gets) == 4, f"expected 4 attempts before raising, got {len(gets)}"


async def test_4xx_other_than_429_does_not_retry() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(method="GET", url_substr="/portals", status=400, body={"message": "bad"})
    )
    async with make_client(rec) as client:
        with pytest.raises(LawstronautClientError):
            await client.list_portals(iso="IE")
    gets = [r for r in rec.requests if r.method == "GET"]
    assert len(gets) == 1, "4xx must not retry"


async def test_429_retries_like_a_transient() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(static_handler(method="GET", url_substr="/jurisdictions", status=429, body="slow"))
    rec.add(
        static_handler(
            method="GET",
            url_substr="/jurisdictions",
            status=200,
            body=load_fixture("jurisdictions_response.json"),
        )
    )
    async with make_client(rec) as client:
        jurisdictions = await client.list_jurisdictions()
    assert any(j.iso == "IE" for j in jurisdictions)
    gets = [r for r in rec.requests if r.method == "GET"]
    assert len(gets) == 2


# --- quirk normalisation ----------------------------------------------------


async def test_get_markdown_prefers_content_markdown_field() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/contents/markdown",
            status=200,
            body=load_fixture("contents_markdown_content_markdown_field.json"),
        )
    )
    async with make_client(rec) as client:
        doc = await client.get_markdown("27732019")
    assert doc.document_id == "27732019"
    assert doc.markdown.startswith("**PART 1**")


async def test_get_markdown_falls_back_to_markdown_field() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/contents/markdown",
            status=200,
            body=load_fixture("contents_markdown_markdown_field.json"),
        )
    )
    async with make_client(rec) as client:
        doc = await client.get_markdown("1018301")
    assert doc.document_id == "1018301"
    assert "Hoofdstuk" in doc.markdown


async def test_get_markdown_coerces_int_document_id_to_str() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/contents/markdown",
            status=200,
            body=load_fixture("contents_markdown_int_document_id.json"),
        )
    )
    async with make_client(rec) as client:
        doc = await client.get_markdown("34659134")
    assert isinstance(doc.document_id, str)
    assert doc.document_id == "34659134"


async def test_get_markdown_tolerates_malformed_publication_date() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/contents/markdown",
            status=200,
            body=load_fixture("contents_markdown_malformed_pub_date.json"),
        )
    )
    async with make_client(rec) as client:
        doc = await client.get_markdown("999000")
    # Either normalised to a real datetime, or returned as None — both are
    # acceptable per docs/api/operational-notes.md; the key property is
    # that no exception escapes.
    assert doc.publication_date is None or doc.publication_date.year == 2026


async def test_get_markdown_empty_data_returns_none() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/contents/markdown",
            status=200,
            body=load_fixture("contents_markdown_empty.json"),
        )
    )
    async with make_client(rec) as client:
        doc = await client.get_markdown("nonexistent")
    assert doc is None


# --- list endpoints ---------------------------------------------------------


async def test_list_jurisdictions_returns_parsed_models() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/jurisdictions",
            status=200,
            body=load_fixture("jurisdictions_response.json"),
        )
    )
    async with make_client(rec) as client:
        jurisdictions = await client.list_jurisdictions()
    assert all(isinstance(j, Jurisdiction) for j in jurisdictions)
    isos = {j.iso for j in jurisdictions}
    assert isos == {"AU", "IE", "US_AL"}


async def test_list_portals_requires_iso_in_request() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/portals",
            status=200,
            body=load_fixture("portals_response.json"),
        )
    )
    async with make_client(rec) as client:
        portals = await client.list_portals(iso="IE")
    assert all(isinstance(p, Portal) for p in portals)
    portal_request = [r for r in rec.requests if "/portals" in str(r.url)][0]
    assert "iso=IE" in str(portal_request.url)


async def test_list_portals_tolerates_string_total_links() -> None:
    rec = CallRecorder()
    rec.add(login_handler())
    rec.add(
        static_handler(
            method="GET",
            url_substr="/portals",
            status=200,
            body=load_fixture("portals_response.json"),
        )
    )
    async with make_client(rec) as client:
        portals = await client.list_portals(iso="IE")
    by_name = {p.name: p for p in portals}
    assert by_name["Irish Statute Book"].total_links == 26888
    assert by_name["Oireachtas"].total_links == 1234

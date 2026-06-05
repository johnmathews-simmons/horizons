"""Async Lawstronaut v2 client with token-refresh seam and retry policy.

See ``client.md`` for the design overview. Highlights worth carrying in
the code:

- Auth lives on a different host (``filerskeepersapi.co``) from the
  data plane (``api.lawstronaut.com/v2``). The two hosts are independent
  constructor parameters so tests point at fixture transports.
- Login returns the bearer at ``payload.data.token.refresh_token`` —
  the field name is misleading on purpose; that token IS the bearer
  used on subsequent calls.
- Pre-emptive refresh is driven by a **monotonic** clock (injectable
  for tests). Refresh fires when ``clock() >= expires_at - refresh_buffer_s``.
  Default buffer is 300 s, so on a 1800 s TTL the refresh lands at the
  25-minute mark, well before the 30-minute hard expiry.
- A per-instance ``asyncio.Lock`` (constructed in ``__init__``, not at
  module import — pytest-asyncio creates a fresh event loop per test
  and a module-level lock binds to whichever loop imports the module
  first) serialises refreshes; concurrent callers see exactly one
  login.
- ``stamina.retry_context`` wraps every HTTP call. Transient errors
  (5xx, 429, network timeouts) retry; auth errors (401, 403) and other
  4xx escape immediately.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Self, cast

import httpx
import stamina

from horizons_core.core.lawstronaut.errors import (
    LawstronautAuthError,
    LawstronautClientError,
    LawstronautError,
    LawstronautTransientError,
)
from horizons_core.core.lawstronaut.models import (
    Credentials,
    Jurisdiction,
    MarkdownDocument,
    Portal,
)

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_API_BASE = "https://api.lawstronaut.com/v2"
DEFAULT_AUTH_URL = "https://filerskeepersapi.co/auth/login"
DEFAULT_REFRESH_BUFFER_S = 300.0
DEFAULT_TIMEOUT_S = 30.0


class LawstronautClient:
    """Async HTTP client wrapping the Lawstronaut v2 surface."""

    def __init__(
        self,
        *,
        credentials: Credentials,
        base_url: str = DEFAULT_API_BASE,
        auth_url: str = DEFAULT_AUTH_URL,
        clock: Callable[[], float] | None = None,
        refresh_buffer_s: float = DEFAULT_REFRESH_BUFFER_S,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        stamina_attempts: int = 4,
        stamina_wait_initial: float = 0.5,
        stamina_wait_max: float = 8.0,
        stamina_wait_jitter: float = 0.2,
    ) -> None:
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")
        self._auth_url = auth_url
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._refresh_buffer_s = refresh_buffer_s
        self._transport = transport
        self._timeout = httpx.Timeout(timeout_s)
        self._stamina_attempts = stamina_attempts
        self._stamina_wait_initial = stamina_wait_initial
        self._stamina_wait_max = stamina_wait_max
        self._stamina_wait_jitter = stamina_wait_jitter

        self._bearer: str | None = None
        self._token_expires_at: float | None = None
        self._refresh_lock = asyncio.Lock()
        self._http: httpx.AsyncClient | None = None

    # --- lifecycle ----------------------------------------------------------

    async def __aenter__(self) -> Self:
        self._http = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
        return self._http

    # --- auth ---------------------------------------------------------------

    def _needs_refresh(self) -> bool:
        if self._bearer is None or self._token_expires_at is None:
            return True
        return self._clock() >= self._token_expires_at - self._refresh_buffer_s

    async def _ensure_fresh_token(self) -> None:
        if not self._needs_refresh():
            return
        async with self._refresh_lock:
            if not self._needs_refresh():
                # Another coroutine refreshed while we were waiting.
                return
            await self.login()

    async def login(self) -> None:
        """POST the credentials and cache the resulting bearer.

        Public so callers can pre-warm before issuing concurrent calls;
        normally the client logs in lazily on first use.
        """
        payload = await self._request_with_retry(
            "POST",
            self._auth_url,
            json={
                "email": self._credentials.email,
                "password": self._credentials.password.get_secret_value(),
            },
            authed=False,
        )
        if not isinstance(payload, dict):
            raise LawstronautAuthError("login response was not a JSON object")
        payload_d = cast("dict[str, Any]", payload)
        data_raw = payload_d.get("data")
        data = cast("dict[str, Any]", data_raw) if isinstance(data_raw, dict) else payload_d
        token_raw = data.get("token")
        token_blob = cast("dict[str, Any]", token_raw) if isinstance(token_raw, dict) else data
        bearer = (
            token_blob.get("refresh_token")
            or token_blob.get("access_token")
            or token_blob.get("token")
        )
        if not isinstance(bearer, str) or not bearer:
            raise LawstronautAuthError("login response missing bearer string")
        expires_in_raw = (
            token_blob.get("expires_in")
            or data.get("expires_in")
            or payload_d.get("expires_in")
            or 1800
        )
        try:
            expires_in = float(expires_in_raw)
        except (TypeError, ValueError) as exc:
            raise LawstronautAuthError("login response has non-numeric expires_in") from exc
        self._bearer = bearer
        self._token_expires_at = self._clock() + expires_in

    async def refresh(self) -> None:
        """Re-mint the bearer.

        Lawstronaut's documented `/auth/refresh-token` endpoint has known
        URL issues and the bearer returned by ``login`` already plays
        the refresh-token role. The pragmatic implementation is to
        re-login.
        """
        await self.login()

    # --- HTTP boundary ------------------------------------------------------

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        authed: bool = True,
    ) -> Any:
        """Issue one request with the stamina retry envelope.

        Transient errors retry; auth and other client errors escape on
        the first attempt. ``authed=True`` adds the bearer header and
        ensures the token is fresh before each attempt.
        """
        result: Any = None
        async for attempt in stamina.retry_context(
            on=LawstronautTransientError,
            attempts=self._stamina_attempts,
            wait_initial=self._stamina_wait_initial,
            wait_max=self._stamina_wait_max,
            wait_jitter=self._stamina_wait_jitter,
        ):
            with attempt:
                if authed:
                    await self._ensure_fresh_token()
                result = await self._do_request(
                    method=method,
                    url=url,
                    json=json,
                    params=params,
                    authed=authed,
                )
        return result

    async def _do_request(
        self,
        *,
        method: str,
        url: str,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
        authed: bool,
    ) -> Any:
        headers: dict[str, str] = {}
        if authed:
            if self._bearer is None:  # pragma: no cover - guarded by _ensure_fresh_token
                raise LawstronautAuthError("authed request issued without a bearer")
            headers["Authorization"] = f"Bearer {self._bearer}"
        try:
            resp = await self._client().request(
                method,
                url,
                json=json,
                params=params,
                headers=headers or None,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise LawstronautTransientError(
                f"network failure on {method} {url}: {exc!r}",
                status=None,
            ) from exc
        return self._consume(resp, method, url)

    def _consume(self, resp: httpx.Response, method: str, url: str) -> Any:
        status = resp.status_code
        if 200 <= status < 300:
            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError as exc:
                raise LawstronautError(
                    f"non-JSON 2xx response on {method} {url}",
                    status=status,
                ) from exc
        snippet = resp.text[:200]
        if status in (401, 403):
            raise LawstronautAuthError(
                f"{status} on {method} {url}: {snippet}",
                status=status,
            )
        if status == 429 or 500 <= status < 600:
            raise LawstronautTransientError(
                f"{status} on {method} {url}: {snippet}",
                status=status,
            )
        if 400 <= status < 500:
            raise LawstronautClientError(
                f"{status} on {method} {url}: {snippet}",
                status=status,
            )
        # Anything else (1xx, 3xx leaking through) is unexpected.
        raise LawstronautError(
            f"unexpected {status} on {method} {url}: {snippet}",
            status=status,
        )

    # --- public data-plane methods -----------------------------------------

    async def get_markdown(self, document_id: str) -> MarkdownDocument | None:
        """Fetch the current markdown for one document.

        Returns ``None`` when the API responds with an empty ``data``
        array — caller decides whether that is a soft "not found"
        (worker logs and bumps next_poll_at) or an indictment of the
        scheduling assumption.
        """
        url = f"{self._base_url}/contents/markdown"
        payload = await self._request_with_retry(
            "GET",
            url,
            params={"document_id": document_id, "limit": 1},
        )
        records = self._extract_data(payload)
        if not records:
            return None
        return MarkdownDocument.model_validate(records[0])

    async def list_jurisdictions(self) -> list[Jurisdiction]:
        """Enumerate every jurisdiction Lawstronaut serves."""
        url = f"{self._base_url}/jurisdictions"
        payload = await self._request_with_retry("GET", url)
        records = self._extract_data(payload)
        return [Jurisdiction.model_validate(r) for r in records]

    async def list_portals(self, *, iso: str) -> list[Portal]:
        """Enumerate portals for one jurisdiction.

        ``iso`` is required: the live API returns 400 when omitted even
        though the docs say it is optional (see
        ``docs/api/operational-notes.md`` §"`/v2/portals` requires `iso`").
        Surfacing it on the signature is the contract.
        """
        url = f"{self._base_url}/portals"
        payload = await self._request_with_retry("GET", url, params={"iso": iso})
        records = self._extract_data(payload)
        return [Portal.model_validate(r) for r in records]

    @staticmethod
    def _extract_data(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        payload_d = cast("dict[str, Any]", payload)
        data = payload_d.get("data")
        if isinstance(data, list):
            data_list = cast("list[Any]", data)
            return [cast("dict[str, Any]", r) for r in data_list if isinstance(r, dict)]
        if isinstance(data, dict):
            return [cast("dict[str, Any]", data)]
        return []

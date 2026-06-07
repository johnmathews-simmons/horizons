"""CORS regression tests for the public API.

The headline regression: on 2026-06-07 the webapp homepage failed to
render because ``GET /v1/me/overview`` returned a 500 (missing
``app_public.change_event_shape()`` SQL function in the deployed DB)
whose response carried no CORS headers — so the browser reported it as
"CORS Missing Allow Origin" and the actual 500 was invisible to anyone
looking at the network panel.

Two failure modes were folded together:

1. ``HORIZONS_CORS_ORIGINS`` could be misconfigured at deploy time,
   producing a real CORS failure on the first request from the SPA.
2. An unhandled exception in a route bubbled past ``CORSMiddleware`` to
   Starlette's outer ``ServerErrorMiddleware``, generating a 21-byte
   "Internal Server Error" body **above** the CORS layer — so the 500
   landed at the browser without ``Access-Control-Allow-Origin`` and
   masqueraded as a CORS bug.

These tests pin both behaviours. They run without a database (pure
``TestClient``) so they belong in the unit lane.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

ISSUER = "horizons-api-test"
AUDIENCE = "horizons-clients-test"

# Mirrors the staging shape: the SPA lives behind Azure Front Door,
# the API lives at a different Container Apps hostname. Cross-origin.
WEBAPP_ORIGIN = "https://horizons-dev-crffaqcedbc7b4gk.z03.azurefd.net"
OTHER_WEBAPP_ORIGIN = "https://horizons-localdev.example.com"
EVIL_ORIGIN = "https://evil.example.com"


@pytest.fixture
def rsa_pems() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@pytest.fixture
def configured_env(
    rsa_pems: tuple[bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_pem, public_pem = rsa_pems
    monkeypatch.setenv("HORIZONS_JWT_PRIVATE_KEY_PEM", private_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_PUBLIC_KEY_PEM", public_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("HORIZONS_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv(
        "HORIZONS_CORS_ORIGINS",
        f"{WEBAPP_ORIGIN},{OTHER_WEBAPP_ORIGIN}",
    )
    monkeypatch.delenv("HORIZONS_DB_URL", raising=False)

    from horizons_api.deps.provider import reset_provider_for_tests

    reset_provider_for_tests()


@pytest.fixture
def client(configured_env: None) -> Iterator[TestClient]:
    _ = configured_env
    from horizons_api.app import create_app

    app = create_app()

    # Adversarial route that exercises the 500 → CORS path. Lives only
    # inside the test app, never registered in production.
    @app.get("/_test/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("intentional unhandled exception")

    # Don't let TestClient swallow the exception itself — we want to
    # see the JSONResponse that the exception handler produces, with
    # the CORS headers attached on the way back through.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_preflight_options_succeeds_for_configured_origin(client: TestClient) -> None:
    """Browser preflight for a real cross-origin call returns 200 + headers."""
    response = client.options(
        "/v1/me",
        headers={
            "Origin": WEBAPP_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == WEBAPP_ORIGIN
    assert response.headers.get("access-control-allow-credentials") == "true"
    allowed_methods = response.headers.get("access-control-allow-methods", "")
    assert "GET" in allowed_methods


def test_simple_response_includes_cors_header_for_configured_origin(
    client: TestClient,
) -> None:
    """A normal 2xx response from a configured origin carries ACAO."""
    response = client.get("/healthz", headers={"Origin": WEBAPP_ORIGIN})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == WEBAPP_ORIGIN


def test_500_response_includes_cors_header_for_configured_origin(
    client: TestClient,
) -> None:
    """Regression: unhandled exceptions must produce a 500 with CORS headers.

    Without the ``Exception`` handler registered in ``app.create_app``,
    the response would be Starlette's bare ``Internal Server Error``
    plain-text body produced **above** ``CORSMiddleware`` — the browser
    would then report a CORS error and the real 500 would be invisible.
    """
    response = client.get("/_test/boom", headers={"Origin": WEBAPP_ORIGIN})
    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == WEBAPP_ORIGIN
    assert response.json() == {"detail": "Internal Server Error"}


def test_unconfigured_origin_does_not_receive_cors_header(client: TestClient) -> None:
    """An origin not on the allowlist is not echoed back."""
    response = client.get("/healthz", headers={"Origin": EVIL_ORIGIN})
    # The request still succeeds at the ASGI layer (CORS is enforced by
    # the *browser*, not the server) — but no ACAO header means the
    # browser will reject the response.
    assert response.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_second_configured_origin_is_also_allowed(client: TestClient) -> None:
    """The allowlist supports multiple origins (comma-separated env var)."""
    response = client.get("/healthz", headers={"Origin": OTHER_WEBAPP_ORIGIN})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == OTHER_WEBAPP_ORIGIN

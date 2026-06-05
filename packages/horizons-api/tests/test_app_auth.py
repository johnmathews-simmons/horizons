"""Integration tests for the FastAPI app shell + auth middleware (WU4.1).

The three acceptance assertions from the work-unit spec:

- Missing bearer → ``401``.
- Invalid bearer → ``401``.
- Valid bearer (issued by the same provider the app uses) → ``200``
  with the stub ``/v1/me`` body echoing the principal.

Plus a few near-bonus checks:

- ``/healthz`` returns 200 with no bearer — proves the unauthenticated
  surface works and the dependency tree is not eager.
- ``/healthz`` does **not** hit the database — the test deliberately
  unsets ``HORIZONS_DB_URL`` for that case to prove the route doesn't
  resolve the engine lazily on first request.

Test uses ``fastapi.testclient.TestClient`` (sync) against an app
constructed with a fresh ephemeral RSA keypair injected via env vars,
so each test case has a clean provider.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from horizons_core.core.auth import LocalJwtProvider, TokenKind

if TYPE_CHECKING:
    from collections.abc import Iterator

ISSUER = "horizons-api-test"
AUDIENCE = "horizons-clients-test"


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
) -> tuple[bytes, bytes]:
    """Populate the env vars the app reads at construction.

    Also resets the provider singleton so the ``create_app`` call below
    picks up the fresh keys instead of a cached provider from a prior
    test in the same process.
    """
    private_pem, public_pem = rsa_pems
    monkeypatch.setenv("HORIZONS_JWT_PRIVATE_KEY_PEM", private_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_PUBLIC_KEY_PEM", public_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("HORIZONS_JWT_AUDIENCE", AUDIENCE)
    # Force a deterministic CORS posture so no inherited env leaks
    # cross-test.
    monkeypatch.setenv("HORIZONS_CORS_ORIGINS", "")
    # DB URL deliberately absent — auth endpoints in this WU don't open
    # a session.
    monkeypatch.delenv("HORIZONS_DB_URL", raising=False)

    from horizons_api.deps.provider import reset_provider_for_tests

    reset_provider_for_tests()
    return private_pem, public_pem


@pytest.fixture
def client(configured_env: tuple[bytes, bytes]) -> Iterator[TestClient]:
    _ = configured_env  # fixture order — env must be set first
    # Re-import so the create_app call sees the env set above; importing
    # at module scope would have run create_app's settings load against
    # the *original* environment.
    from horizons_api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_healthz_returns_200_unauthenticated_and_does_not_touch_db(
    client: TestClient,
) -> None:
    """``/healthz`` works without a bearer and without a DB URL set."""
    # Pre-condition: this fixture deletes HORIZONS_DB_URL. If the route
    # touched the DB the engine construction would raise KeyError.
    assert "HORIZONS_DB_URL" not in os.environ
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_bearer_returns_401(client: TestClient) -> None:
    response = client.get("/v1/me")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_malformed_authorization_header_returns_401(client: TestClient) -> None:
    """Header present but not ``Bearer <token>`` → 401 (not 422)."""
    response = client.get("/v1/me", headers={"Authorization": "Basic notbearer"})
    assert response.status_code == 401


def test_invalid_bearer_returns_401(client: TestClient) -> None:
    response = client.get("/v1/me", headers={"Authorization": "Bearer not.a.real.jwt"})
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_bearer_signed_by_other_keypair_returns_401(
    configured_env: tuple[bytes, bytes],
    client: TestClient,
) -> None:
    """A JWT minted by a different keypair fails signature verification."""
    _ = configured_env
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_private = other_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    other_public = other_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    rogue_provider = LocalJwtProvider(
        private_key=other_private,
        public_key=other_public,
        issuer=ISSUER,
        audience=AUDIENCE,
    )

    import asyncio

    token = asyncio.run(
        rogue_provider.issue_token(
            user_id=uuid.uuid4(),
            role="client",
            kind=TokenKind.ACCESS,
        )
    )

    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_valid_bearer_returns_200_and_principal_body(
    configured_env: tuple[bytes, bytes],
    client: TestClient,
) -> None:
    """A token issued by the same provider the app uses → 200 + body."""
    private_pem, public_pem = configured_env
    provider = LocalJwtProvider(
        private_key=private_pem,
        public_key=public_pem,
        issuer=ISSUER,
        audience=AUDIENCE,
    )
    user_id = uuid.uuid4()
    import asyncio

    token = asyncio.run(
        provider.issue_token(
            user_id=user_id,
            role="client",
            kind=TokenKind.ACCESS,
        )
    )

    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "user_id": str(user_id),
        "role": "client",
        "kind": "access",
    }


def _issue_token_with_kind(
    private_pem: bytes,
    public_pem: bytes,
    kind: TokenKind,
) -> str:
    """Mint a token of the requested kind with the test provider's keys."""
    provider = LocalJwtProvider(
        private_key=private_pem,
        public_key=public_pem,
        issuer=ISSUER,
        audience=AUDIENCE,
    )
    import asyncio

    # IMPERSONATION tokens — like ACCESS — don't write to refresh_tokens,
    # so no session is needed. REFRESH tokens need a session but the
    # tests for those use the integration suite; here we only need
    # ACCESS and IMPERSONATION to verify the kind gate.
    return asyncio.run(
        provider.issue_token(
            user_id=uuid.uuid4(),
            role="client",
            kind=kind,
        )
    )


def test_refresh_token_rejected_at_me_endpoint(
    configured_env: tuple[bytes, bytes],
    client: TestClient,
) -> None:
    """A REFRESH token must not authenticate /v1/me.

    Forges the JWT directly via ``jwt.encode`` rather than
    ``LocalJwtProvider.issue_token(kind=REFRESH)`` because the latter
    requires a DB session to persist the refresh-tokens row — and the
    point of the test is the *kind-claim check at the auth boundary*,
    which is upstream of any DB write. Regression test for the
    missing-authz finding from the push-time security review.
    """
    import time

    import jwt

    private_pem, _ = configured_env
    now = int(time.time())
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "client",
        "kind": TokenKind.REFRESH.value,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 60 * 60,
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    token = jwt.encode(payload, private_pem, algorithm="RS256")

    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid bearer token"}


def test_impersonation_token_rejected_at_me_endpoint(
    configured_env: tuple[bytes, bytes],
    client: TestClient,
) -> None:
    """An IMPERSONATION token must not authenticate /v1/me.

    /v1/me depends on require_kind(ACCESS). An impersonation token is
    a valid JWT against the configured keypair, so the signature /
    issuer / audience / expiry all pass; the kind gate is the only
    defence. Regression test for the missing-authz finding from the
    push-time security review.
    """
    private_pem, public_pem = configured_env
    token = _issue_token_with_kind(private_pem, public_pem, TokenKind.IMPERSONATION)
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    # Body must not distinguish wrong-kind from invalid-signature —
    # the client cannot probe the verifier's branches.
    assert response.json() == {"detail": "invalid bearer token"}


def test_missing_required_env_var_fails_app_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_app`` must refuse to start without the JWT key env vars.

    Silently substituting a weak default would be a worse failure mode
    than a loud startup error — the operator finds out immediately.
    """
    for name in (
        "HORIZONS_JWT_PRIVATE_KEY_PEM",
        "HORIZONS_JWT_PUBLIC_KEY_PEM",
        "HORIZONS_JWT_ISSUER",
        "HORIZONS_JWT_AUDIENCE",
    ):
        monkeypatch.delenv(name, raising=False)

    from horizons_api.app import create_app

    with pytest.raises(RuntimeError, match="HORIZONS_JWT_PRIVATE_KEY_PEM"):
        create_app()

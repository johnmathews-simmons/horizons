# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# Rationale: httpx (and starlette's TestClient that wraps it) ships PEP-561
# stubs that strict pyright treats as Unknown for the response / cookies /
# headers surfaces. The runtime types are well-known and tested by the
# very assertions below; suppressing at file level matches the posture
# already established in ``packages/horizons-api/tests/test_app_auth.py``
# (which is excluded from the pyright include list for the same reason).
"""Integration tests for WU4.2 — ``POST /v1/auth/{login,refresh,logout}``.

Each flow is exercised in both postures the contract supports: the
programmatic shape (JSON tokens, ``Authorization: Bearer`` source) and
the browser shape (``X-Client-Type: browser``, ``HttpOnly`` cookie
source). Coverage:

- Login programmatic: 200 with both tokens; ``Cache-Control: private,
  no-store``; refresh-token row persisted.
- Login browser: 200 with access only; refresh-token in
  ``HttpOnly; Secure; SameSite=Lax; Path=/v1/auth`` cookie; refresh-token
  row persisted.
- Login wrong password / unknown email: 401 with uniform body.
- Refresh programmatic: 200, old jti revoked, new tokens issued.
- Refresh browser: 200, new ``Set-Cookie`` with the rotated refresh.
- Refresh after revoke (replay): 401.
- Refresh with access-kind token: 401 (kind gate).
- Logout programmatic: 204, jti revoked.
- Logout browser: 204, clearing ``Set-Cookie`` issued.

The DB is the session-scoped testcontainer from ``tests/conftest.py``.
Each test seeds its own user with a unique email to keep cases
independent under the shared schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from horizons_core.core.auth import hash_password
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

ISSUER = "horizons-api-auth-test"
AUDIENCE = "horizons-clients-auth-test"


@pytest.fixture(scope="session")
def migrated_postgres(postgres_container: PostgresContainer) -> Iterator[Engine]:
    """Apply Alembic head against the session-scoped container."""
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    cfg = Config(str(ALEMBIC_INI))
    # Alembic env.py reads HORIZONS_DB_URL; override here so the migration
    # tree targets the testcontainer.
    import os

    prev = os.environ.get("HORIZONS_DB_URL")
    os.environ["HORIZONS_DB_URL"] = sync_url
    try:
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("HORIZONS_DB_URL", None)
        else:
            os.environ["HORIZONS_DB_URL"] = prev
    eng = create_engine(sync_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


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
    migrated_postgres: Engine,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, bytes]:
    _ = migrated_postgres  # ensure migrations applied before app starts
    private_pem, public_pem = rsa_pems
    monkeypatch.setenv("HORIZONS_JWT_PRIVATE_KEY_PEM", private_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_PUBLIC_KEY_PEM", public_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("HORIZONS_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("HORIZONS_CORS_ORIGINS", "")
    monkeypatch.setenv(
        "HORIZONS_DB_URL",
        postgres_container.get_connection_url(driver="asyncpg"),
    )

    from horizons_api.deps.provider import reset_provider_for_tests
    from horizons_core.db import session as session_mod

    reset_provider_for_tests()
    # The lazy engine in horizons_core.db.session caches across tests in
    # the same process; reset it so the new HORIZONS_DB_URL takes effect.
    session_mod._engine = None  # type: ignore[attr-defined]  # noqa: SLF001
    return private_pem, public_pem


@pytest.fixture
def client(configured_env: tuple[bytes, bytes]) -> Iterator[TestClient]:
    _ = configured_env
    from horizons_api.app import create_app

    app = create_app()
    # ``base_url`` is https because the refresh-token cookie carries the
    # ``Secure`` attribute and would not be sent back to the server over
    # plain http — the browser flow tests would silently degrade.
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _seed_user(
    engine: Engine,
    email: str,
    password: str,
    role: str = "client",
) -> str:
    """Insert a ``users`` row with a real argon2 hash; return the id."""
    hashed = hash_password(password)
    with engine.begin() as conn:
        uid = conn.execute(
            text("INSERT INTO users (email, password_hash, role) VALUES (:e, :p, :r) RETURNING id"),
            {"e": email, "p": hashed, "r": role},
        ).scalar_one()
    return str(uid)


def _refresh_row(engine: Engine, jti: str) -> dict[str, object] | None:
    """Return a refresh_tokens row by jti for assertions; ``None`` if absent."""
    with engine.connect() as conn:
        row = (
            conn.execute(
                text("SELECT jti, user_id, revoked_at FROM refresh_tokens WHERE jti = :j"),
                {"j": jti},
            )
            .mappings()
            .one_or_none()
        )
    return dict(row) if row else None


def _decode_jti(token: str, public_pem: bytes) -> str:
    """Decode a JWT without enforcing iss / aud — we only need the jti."""
    import jwt

    payload = jwt.decode(
        token,
        public_pem,
        algorithms=["RS256"],
        options={"verify_aud": False, "verify_iss": False},
    )
    return str(payload["jti"])


# ---- login -------------------------------------------------------------------


@pytest.mark.integration
def test_login_programmatic_returns_both_tokens_and_writes_refresh_row(
    client: TestClient,
    migrated_postgres: Engine,
    configured_env: tuple[bytes, bytes],
) -> None:
    _, public_pem = configured_env
    _seed_user(migrated_postgres, "login_prog@example.com", "pass-correct")

    response = client.post(
        "/v1/auth/login",
        json={"email": "login_prog@example.com", "password": "pass-correct"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "access_token" in body and body["access_token"]
    assert "refresh_token" in body and body["refresh_token"]
    assert response.headers.get("Cache-Control") == "private, no-store"
    # Programmatic flow: no cookie.
    assert "set-cookie" not in {h.lower() for h in response.headers}

    jti = _decode_jti(body["refresh_token"], public_pem)
    row = _refresh_row(migrated_postgres, jti)
    assert row is not None
    assert row["revoked_at"] is None


@pytest.mark.integration
def test_login_browser_sets_httponly_cookie_and_omits_refresh_from_body(
    client: TestClient,
    migrated_postgres: Engine,
    configured_env: tuple[bytes, bytes],
) -> None:
    _, public_pem = configured_env
    _seed_user(migrated_postgres, "login_browser@example.com", "pass-browser")

    response = client.post(
        "/v1/auth/login",
        json={"email": "login_browser@example.com", "password": "pass-browser"},
        headers={"X-Client-Type": "browser"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body.get("refresh_token") is None
    assert response.headers.get("Cache-Control") == "private, no-store"

    set_cookie = response.headers.get("set-cookie", "")
    lowered = set_cookie.lower()
    assert "refresh_token=" in set_cookie
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    assert "path=/v1/auth" in lowered

    refresh_cookie = client.cookies.get("refresh_token")
    assert refresh_cookie
    jti = _decode_jti(refresh_cookie, public_pem)
    row = _refresh_row(migrated_postgres, jti)
    assert row is not None
    assert row["revoked_at"] is None


@pytest.mark.integration
def test_login_wrong_password_returns_401_uniform(
    client: TestClient,
    migrated_postgres: Engine,
) -> None:
    _seed_user(migrated_postgres, "login_bad_pw@example.com", "the-real-password")
    response = client.post(
        "/v1/auth/login",
        json={"email": "login_bad_pw@example.com", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}


@pytest.mark.integration
def test_login_unknown_email_returns_401_uniform(
    client: TestClient,
) -> None:
    """Same body as wrong password so account existence is not probable."""
    response = client.post(
        "/v1/auth/login",
        json={"email": "nobody_at_all@example.com", "password": "anything"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}


# ---- refresh -----------------------------------------------------------------


@pytest.mark.integration
def test_refresh_programmatic_rotates_and_revokes_old(
    client: TestClient,
    migrated_postgres: Engine,
    configured_env: tuple[bytes, bytes],
) -> None:
    _, public_pem = configured_env
    _seed_user(migrated_postgres, "refresh_prog@example.com", "pw")
    login = client.post(
        "/v1/auth/login",
        json={"email": "refresh_prog@example.com", "password": "pw"},
    )
    old_refresh = login.json()["refresh_token"]
    old_jti = _decode_jti(old_refresh, public_pem)

    response = client.post(
        "/v1/auth/refresh",
        headers={"Authorization": f"Bearer {old_refresh}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["refresh_token"] != old_refresh

    # Old jti revoked; new jti present and live.
    old_row = _refresh_row(migrated_postgres, old_jti)
    assert old_row is not None
    assert old_row["revoked_at"] is not None
    new_jti = _decode_jti(body["refresh_token"], public_pem)
    new_row = _refresh_row(migrated_postgres, new_jti)
    assert new_row is not None
    assert new_row["revoked_at"] is None


@pytest.mark.integration
def test_refresh_browser_rotates_cookie(
    client: TestClient,
    migrated_postgres: Engine,
    configured_env: tuple[bytes, bytes],
) -> None:
    _, public_pem = configured_env
    _seed_user(migrated_postgres, "refresh_br@example.com", "pw")
    client.post(
        "/v1/auth/login",
        json={"email": "refresh_br@example.com", "password": "pw"},
        headers={"X-Client-Type": "browser"},
    )
    old_cookie = client.cookies.get("refresh_token")
    assert old_cookie

    response = client.post(
        "/v1/auth/refresh",
        headers={"X-Client-Type": "browser"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.json().get("refresh_token") is None
    set_cookie = response.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "httponly" in set_cookie.lower()

    new_cookie = client.cookies.get("refresh_token")
    assert new_cookie and new_cookie != old_cookie
    new_jti = _decode_jti(new_cookie, public_pem)
    new_row = _refresh_row(migrated_postgres, new_jti)
    assert new_row is not None
    assert new_row["revoked_at"] is None


@pytest.mark.integration
def test_refresh_replay_after_revoke_returns_401(
    client: TestClient,
    migrated_postgres: Engine,
) -> None:
    _seed_user(migrated_postgres, "refresh_replay@example.com", "pw")
    login = client.post(
        "/v1/auth/login",
        json={"email": "refresh_replay@example.com", "password": "pw"},
    )
    old_refresh = login.json()["refresh_token"]

    first = client.post(
        "/v1/auth/refresh",
        headers={"Authorization": f"Bearer {old_refresh}"},
    )
    assert first.status_code == 200

    # Same refresh token presented again — already revoked.
    replay = client.post(
        "/v1/auth/refresh",
        headers={"Authorization": f"Bearer {old_refresh}"},
    )
    assert replay.status_code == 401


@pytest.mark.integration
def test_refresh_rejects_access_kind_token(
    client: TestClient,
    migrated_postgres: Engine,
) -> None:
    """An access token presented to /v1/auth/refresh must 401."""
    _seed_user(migrated_postgres, "refresh_wrong_kind@example.com", "pw")
    login = client.post(
        "/v1/auth/login",
        json={"email": "refresh_wrong_kind@example.com", "password": "pw"},
    )
    access = login.json()["access_token"]

    response = client.post(
        "/v1/auth/refresh",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid refresh token"}


@pytest.mark.integration
def test_refresh_missing_token_returns_401(client: TestClient) -> None:
    response = client.post("/v1/auth/refresh")
    assert response.status_code == 401


# ---- logout ------------------------------------------------------------------


@pytest.mark.integration
def test_logout_programmatic_revokes_jti(
    client: TestClient,
    migrated_postgres: Engine,
    configured_env: tuple[bytes, bytes],
) -> None:
    _, public_pem = configured_env
    _seed_user(migrated_postgres, "logout_prog@example.com", "pw")
    login = client.post(
        "/v1/auth/login",
        json={"email": "logout_prog@example.com", "password": "pw"},
    )
    refresh_token = login.json()["refresh_token"]
    jti = _decode_jti(refresh_token, public_pem)

    response = client.post(
        "/v1/auth/logout",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )

    assert response.status_code == 204
    assert response.headers.get("Cache-Control") == "private, no-store"
    row = _refresh_row(migrated_postgres, jti)
    assert row is not None
    assert row["revoked_at"] is not None


@pytest.mark.integration
def test_logout_browser_clears_cookie(
    client: TestClient,
    migrated_postgres: Engine,
) -> None:
    _seed_user(migrated_postgres, "logout_br@example.com", "pw")
    client.post(
        "/v1/auth/login",
        json={"email": "logout_br@example.com", "password": "pw"},
        headers={"X-Client-Type": "browser"},
    )
    assert client.cookies.get("refresh_token")

    response = client.post("/v1/auth/logout", headers={"X-Client-Type": "browser"})
    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    # Cleared cookie carries Max-Age=0 / expires in the past so the
    # browser drops it on receipt.
    assert "refresh_token=" in set_cookie
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()


@pytest.mark.integration
def test_logout_missing_token_returns_401(client: TestClient) -> None:
    response = client.post("/v1/auth/logout")
    assert response.status_code == 401

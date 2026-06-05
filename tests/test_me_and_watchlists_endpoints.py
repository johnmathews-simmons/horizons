# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# httpx / TestClient stubs are Unknown under strict pyright; same posture
# as ``packages/horizons-api/tests/test_app_auth.py``.
"""Integration tests for WU4.3 — ``/v1/me`` + ``/v1/me/watchlists``.

Coverage:

- ``GET /v1/me`` — real implementation: returns the user row plus the
  subscription summary (scope + active rows); carries
  ``Cache-Control: private, no-store``.
- ``GET /v1/me/watchlists`` — list (empty initial state), same cache
  header.
- ``POST /v1/me/watchlists`` (in-scope) — 201 + row inserted.
- ``POST /v1/me/watchlists`` (out-of-scope) — 422 from the
  *service-layer* validator. The route never reaches the trigger.
- ``POST /v1/me/watchlists`` (out-of-scope) — *with the service-layer
  check disabled* (direct repository write through a session bracket)
  the database trigger raises. This is the defence-in-depth assertion
  the work-unit acceptance demands.
- ``DELETE /v1/me/watchlists/{id}`` — 204 for own row, 404 for an
  invisible row (RLS filters; we don't leak existence).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import sqlalchemy
import sqlalchemy.exc
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from horizons_core.core.auth import LocalJwtProvider, TokenKind, hash_password
from horizons_core.db.session import make_engine, session_for_user
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

ISSUER = "horizons-api-watchlists-test"
AUDIENCE = "horizons-clients-watchlists-test"


@pytest.fixture(scope="session")
def migrated_postgres_w(postgres_container: PostgresContainer) -> Iterator[Engine]:
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    cfg = Config(str(ALEMBIC_INI))
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
    migrated_postgres_w: Engine,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, bytes]:
    _ = migrated_postgres_w
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
    session_mod._engine = None  # type: ignore[attr-defined]  # noqa: SLF001
    return private_pem, public_pem


@pytest.fixture
def client(configured_env: tuple[bytes, bytes]) -> Iterator[TestClient]:
    _ = configured_env
    from horizons_api.app import create_app

    app = create_app()
    with TestClient(app, base_url="https://testserver") as c:
        yield c


# ---- helpers -----------------------------------------------------------------


def _seed_user_with_scope(
    engine: Engine,
    email: str,
    *,
    scope: tuple[tuple[str, str], ...] = (("ie", "legal"),),
) -> uuid.UUID:
    """Insert a user with one active subscription covering ``scope``."""
    pw_hash = hash_password("pw")
    with engine.begin() as conn:
        uid = conn.execute(
            text(
                "INSERT INTO users (email, password_hash, role) "
                "VALUES (:e, :p, 'client') RETURNING id"
            ),
            {"e": email, "p": pw_hash},
        ).scalar_one()
        sub = conn.execute(
            text(
                "INSERT INTO subscriptions (user_id, valid_from) "
                "VALUES (:u, now() - interval '1 day') RETURNING id"
            ),
            {"u": uid},
        ).scalar_one()
        for j, s in scope:
            conn.execute(
                text(
                    "INSERT INTO subscription_scopes (subscription_id, jurisdiction, sector) "
                    "VALUES (:s, :j, :sec)"
                ),
                {"s": sub, "j": j, "sec": s},
            )
    return uid


def _seed_document(
    engine: Engine,
    lawstronaut_id: str,
    *,
    jurisdiction: str,
    sector: str,
    title: str = "T",
) -> uuid.UUID:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO documents (jurisdiction, sector, lawstronaut_document_id, title) "
                "VALUES (:j, :s, :l, :t) RETURNING id"
            ),
            {"j": jurisdiction, "s": sector, "l": lawstronaut_id, "t": title},
        ).scalar_one()


def _login(client: TestClient, email: str) -> str:
    """Drive the login flow and return a fresh access token."""
    response = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "pw"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- /v1/me ------------------------------------------------------------------


@pytest.mark.integration
def test_get_me_returns_user_and_subscription_summary(
    client: TestClient,
    migrated_postgres_w: Engine,
) -> None:
    _seed_user_with_scope(
        migrated_postgres_w,
        "me_summary@example.com",
        scope=(("ie", "legal"), ("uk", "fintech")),
    )
    token = _login(client, "me_summary@example.com")

    response = client.get("/v1/me", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.headers.get("Cache-Control") == "private, no-store"
    body = response.json()
    assert body["email"] == "me_summary@example.com"
    assert body["role"] == "client"
    assert isinstance(uuid.UUID(body["user_id"]), uuid.UUID)
    assert len(body["subscription"]["active_subscriptions"]) == 1
    pairs = {(p["jurisdiction"], p["sector"]) for p in body["subscription"]["scope"]}
    assert pairs == {("ie", "legal"), ("uk", "fintech")}


# ---- watchlists CRUD ---------------------------------------------------------


@pytest.mark.integration
def test_list_watchlists_initially_empty_with_cache_header(
    client: TestClient,
    migrated_postgres_w: Engine,
) -> None:
    _seed_user_with_scope(migrated_postgres_w, "wl_empty@example.com")
    token = _login(client, "wl_empty@example.com")
    response = client.get("/v1/me/watchlists", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers.get("Cache-Control") == "private, no-store"


@pytest.mark.integration
def test_post_watchlist_in_scope_returns_201_and_persists(
    client: TestClient,
    migrated_postgres_w: Engine,
) -> None:
    _seed_user_with_scope(migrated_postgres_w, "wl_create@example.com")
    doc = _seed_document(
        migrated_postgres_w,
        "wl_create_doc",
        jurisdiction="ie",
        sector="legal",
        title="Companies Act 2014",
    )
    token = _login(client, "wl_create@example.com")

    response = client.post(
        "/v1/me/watchlists",
        headers=_bearer(token),
        json={"document_id": str(doc)},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["document_id"] == str(doc)
    assert body["name"] == "Companies Act 2014"

    listed = client.get("/v1/me/watchlists", headers=_bearer(token)).json()
    assert len(listed) == 1
    assert listed[0]["document_id"] == str(doc)


@pytest.mark.integration
def test_post_watchlist_out_of_scope_returns_422_service_layer(
    client: TestClient,
    migrated_postgres_w: Engine,
) -> None:
    """The user is scoped to ie/legal; the document lives in fr/legal.

    The route's service-layer validator rejects with 422 *before* the
    insert reaches the database — proves the user-facing path produces
    a clean validation error.
    """
    _seed_user_with_scope(migrated_postgres_w, "wl_out_svc@example.com", scope=(("ie", "legal"),))
    out_doc = _seed_document(
        migrated_postgres_w,
        "wl_out_svc_doc",
        jurisdiction="fr",
        sector="legal",
    )
    token = _login(client, "wl_out_svc@example.com")

    response = client.post(
        "/v1/me/watchlists",
        headers=_bearer(token),
        json={"document_id": str(out_doc)},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["detail"] == "document is outside your subscription scope"

    # And no row was inserted at the DB level.
    with migrated_postgres_w.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM watchlists WHERE name = :n"),
            {"n": "T"},
        ).scalar_one()
        assert count == 0


@pytest.mark.integration
async def test_trigger_rejects_out_of_scope_insert_defence_in_depth(
    migrated_postgres_w: Engine,
    postgres_container: PostgresContainer,
) -> None:
    """Bypass the service layer entirely; the trigger must still raise.

    Drives the watchlists repository directly under the same
    ``session_for_user`` + ``SET LOCAL ROLE api_app`` bracket the API
    uses, but skips the route's scope check. The database trigger
    ``watchlists_in_subscription_scope`` is the last line of defence
    and must reject. This is the defence-in-depth half of the
    work-unit acceptance.
    """
    from horizons_core.repos.watchlists import WatchlistsRepository

    uid = _seed_user_with_scope(
        migrated_postgres_w,
        "wl_out_trigger@example.com",
        scope=(("ie", "legal"),),
    )
    out_doc = _seed_document(
        migrated_postgres_w,
        "wl_out_trigger_doc",
        jurisdiction="fr",
        sector="legal",
    )

    async_url = postgres_container.get_connection_url(driver="asyncpg")
    eng = make_engine(async_url)
    try:
        with pytest.raises(sqlalchemy.exc.IntegrityError) as exc_info:
            async with session_for_user(eng, uid) as session:
                await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
                await WatchlistsRepository(session).create(
                    user_id=uid,
                    document_id=out_doc,
                    name="bypass",
                )
        assert "outside subscription scope" in str(exc_info.value)
    finally:
        await eng.dispose()


@pytest.mark.integration
def test_delete_own_watchlist_returns_204(
    client: TestClient,
    migrated_postgres_w: Engine,
) -> None:
    _seed_user_with_scope(migrated_postgres_w, "wl_del@example.com")
    doc = _seed_document(
        migrated_postgres_w,
        "wl_del_doc",
        jurisdiction="ie",
        sector="legal",
    )
    token = _login(client, "wl_del@example.com")
    created = client.post(
        "/v1/me/watchlists",
        headers=_bearer(token),
        json={"document_id": str(doc)},
    ).json()

    response = client.delete(f"/v1/me/watchlists/{created['id']}", headers=_bearer(token))
    assert response.status_code == 204
    assert response.headers.get("Cache-Control") == "private, no-store"

    listed = client.get("/v1/me/watchlists", headers=_bearer(token)).json()
    assert listed == []


@pytest.mark.integration
def test_delete_others_watchlist_returns_404(
    client: TestClient,
    migrated_postgres_w: Engine,
) -> None:
    """B cannot delete A's watchlist; RLS makes it look 'not found'."""
    _seed_user_with_scope(migrated_postgres_w, "wl_delA@example.com")
    _seed_user_with_scope(migrated_postgres_w, "wl_delB@example.com")
    doc = _seed_document(
        migrated_postgres_w,
        "wl_delAB_doc",
        jurisdiction="ie",
        sector="legal",
    )

    token_a = _login(client, "wl_delA@example.com")
    created = client.post(
        "/v1/me/watchlists",
        headers=_bearer(token_a),
        json={"document_id": str(doc)},
    ).json()

    # Drop A's auth and use B's.
    token_b = _login(client, "wl_delB@example.com")
    response = client.delete(f"/v1/me/watchlists/{created['id']}", headers=_bearer(token_b))
    assert response.status_code == 404


# ---- minimal access-control regression for /v1/me ----------------------------


@pytest.mark.integration
def test_v1_me_still_rejects_refresh_token(
    client: TestClient,
    migrated_postgres_w: Engine,
    configured_env: tuple[bytes, bytes],
) -> None:
    """Regression: the kind gate on /v1/me still applies post-WU4.3."""
    private_pem, public_pem = configured_env
    provider = LocalJwtProvider(
        private_key=private_pem,
        public_key=public_pem,
        issuer=ISSUER,
        audience=AUDIENCE,
    )
    import time

    import jwt

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
    _ = provider  # silence: not used directly, but constructed to mirror prod
    response = client.get("/v1/me", headers=_bearer(token))
    assert response.status_code == 401

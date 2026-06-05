# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# httpx / TestClient stubs are Unknown under strict pyright; same posture
# as ``tests/test_me_and_watchlists_endpoints.py``.
"""WU4.5 integration tests — ``/v1/admin/subscriptions``.

Coverage:

1. Admin POSTs a subscription for a client; the client immediately
   sees it via ``GET /v1/me``.
2. Admin PATCHes a subscription to remove a scope; an existing
   watchlist for a document in that scope flips to ``active=false``
   (not deleted). The client's ``GET /v1/me/watchlists`` no longer
   shows it; an admin-bypass SELECT against ``watchlists`` does.
3. Non-admin calling ``/v1/admin/subscriptions`` returns 403 (not 404 —
   ``/v1/admin/*`` is documented administrative).
4. Admin writes (POST, PATCH) each create exactly one
   ``admin_access_log`` row with ``mode='operator'`` and
   ``target_user_id=NULL`` — the WU1.9 contract preserved.
"""

from __future__ import annotations

import uuid
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

ISSUER = "horizons-api-admin-test"
AUDIENCE = "horizons-clients-admin-test"


@pytest.fixture(scope="session")
def migrated_postgres_a(postgres_container: PostgresContainer) -> Iterator[Engine]:
    """Session-scoped migrated Postgres for the admin endpoints suite.

    Distinct from ``migrated_postgres_w`` so the suites don't fight over
    the same ``HORIZONS_DB_URL`` setup-vs-teardown ordering.
    """
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
    migrated_postgres_a: Engine,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, bytes]:
    _ = migrated_postgres_a
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


# ---- seeding helpers --------------------------------------------------------


def _make_user(engine: Engine, email: str, role: str = "client") -> uuid.UUID:
    pw_hash = hash_password("pw")
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO users (email, password_hash, role) VALUES (:e, :p, :r) RETURNING id"),
            {"e": email, "p": pw_hash, "r": role},
        ).scalar_one()


def _seed_initial_subscription(
    engine: Engine,
    user_id: uuid.UUID,
    scope: tuple[tuple[str, str], ...],
) -> uuid.UUID:
    with engine.begin() as conn:
        sub_id = conn.execute(
            text(
                "INSERT INTO subscriptions (user_id, valid_from) "
                "VALUES (:u, now() - interval '1 day') RETURNING id"
            ),
            {"u": user_id},
        ).scalar_one()
        for j, s in scope:
            conn.execute(
                text(
                    "INSERT INTO subscription_scopes "
                    "(subscription_id, jurisdiction, sector) VALUES (:s, :j, :sec)"
                ),
                {"s": sub_id, "j": j, "sec": s},
            )
    return sub_id


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
                "INSERT INTO documents "
                "(jurisdiction, sector, lawstronaut_document_id, title) "
                "VALUES (:j, :s, :l, :t) RETURNING id"
            ),
            {"j": jurisdiction, "s": sector, "l": lawstronaut_id, "t": title},
        ).scalar_one()


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "pw"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _audit_count_for(engine: Engine, admin_id: uuid.UUID) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM admin_access_log WHERE admin_id = :a"),
            {"a": admin_id},
        ).scalar_one()


# ---- 1. POST → /v1/me reflects new subscription ----------------------------


@pytest.mark.integration
def test_admin_post_subscription_shows_up_in_client_me(
    client: TestClient,
    migrated_postgres_a: Engine,
) -> None:
    admin_id = _make_user(migrated_postgres_a, "admin_post@example.com", role="admin")
    target_id = _make_user(migrated_postgres_a, "client_post@example.com", role="client")
    _ = admin_id  # admin_id used implicitly via login

    admin_token = _login(client, "admin_post@example.com")
    response = client.post(
        "/v1/admin/subscriptions",
        headers=_bearer(admin_token),
        json={
            "user_id": str(target_id),
            "scopes": [
                {"jurisdiction": "uk", "sector": "banking"},
                {"jurisdiction": "uk", "sector": "fintech"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_id"] == str(target_id)
    pairs = {(s["jurisdiction"], s["sector"]) for s in body["scopes"]}
    assert pairs == {("uk", "banking"), ("uk", "fintech")}

    # The client logs in and the new scope is reflected in /v1/me.
    client_token = _login(client, "client_post@example.com")
    me = client.get("/v1/me", headers=_bearer(client_token))
    assert me.status_code == 200, me.text
    me_pairs = {(p["jurisdiction"], p["sector"]) for p in me.json()["subscription"]["scope"]}
    assert me_pairs == {("uk", "banking"), ("uk", "fintech")}


# ---- 2. PATCH reduction → watchlist soft-hidden ----------------------------


@pytest.mark.integration
def test_admin_patch_reduction_soft_hides_out_of_scope_watchlist(
    client: TestClient,
    migrated_postgres_a: Engine,
) -> None:
    _make_user(migrated_postgres_a, "admin_red@example.com", role="admin")
    target_id = _make_user(migrated_postgres_a, "client_red@example.com", role="client")
    sub_id = _seed_initial_subscription(
        migrated_postgres_a,
        target_id,
        scope=(("uk", "banking"), ("uk", "fintech")),
    )
    # Two documents — one stays in scope after the reduction, one will not.
    keep_doc = _seed_document(
        migrated_postgres_a,
        "keep_doc",
        jurisdiction="uk",
        sector="banking",
        title="Bank Act",
    )
    drop_doc = _seed_document(
        migrated_postgres_a,
        "drop_doc",
        jurisdiction="uk",
        sector="fintech",
        title="Fintech Rule",
    )

    client_token = _login(client, "client_red@example.com")
    for doc in (keep_doc, drop_doc):
        r = client.post(
            "/v1/me/watchlists",
            headers=_bearer(client_token),
            json={"document_id": str(doc)},
        )
        assert r.status_code == 201, r.text
    pre = client.get("/v1/me/watchlists", headers=_bearer(client_token)).json()
    assert {row["document_id"] for row in pre} == {str(keep_doc), str(drop_doc)}

    # Admin reduces scope by removing uk/fintech.
    admin_token = _login(client, "admin_red@example.com")
    patch = client.patch(
        f"/v1/admin/subscriptions/{sub_id}",
        headers=_bearer(admin_token),
        json={"remove_scopes": [{"jurisdiction": "uk", "sector": "fintech"}]},
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["scopes_added"] == 0
    assert body["scopes_removed"] == 1
    assert body["watchlists_soft_hidden"] == 1
    # The dropped scope row is now ``valid_to != null`` (soft-deleted).
    dropped = next(
        s
        for s in body["subscription"]["scopes"]
        if (s["jurisdiction"], s["sector"]) == ("uk", "fintech")
    )
    assert dropped["valid_to"] is not None

    # Client's GET /v1/me/watchlists no longer returns the out-of-scope row.
    post = client.get("/v1/me/watchlists", headers=_bearer(client_token)).json()
    assert {row["document_id"] for row in post} == {str(keep_doc)}

    # The hidden row is still present at the DB level under admin_bypass —
    # `active = false`, not deleted.
    with migrated_postgres_a.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT document_id, active FROM watchlists WHERE user_id = :u ORDER BY document_id"
            ),
            {"u": target_id},
        ).all()
    by_doc = {str(r[0]): r[1] for r in rows}
    assert by_doc[str(keep_doc)] is True
    assert by_doc[str(drop_doc)] is False


# ---- 3. Non-admin → 403 not 404 --------------------------------------------


@pytest.mark.integration
def test_non_admin_calling_admin_endpoint_returns_403(
    client: TestClient,
    migrated_postgres_a: Engine,
) -> None:
    target_id = _make_user(migrated_postgres_a, "client_for_403@example.com", role="client")
    _seed_initial_subscription(migrated_postgres_a, target_id, scope=(("uk", "banking"),))
    token = _login(client, "client_for_403@example.com")

    for resp in (
        client.get(
            "/v1/admin/subscriptions",
            headers=_bearer(token),
            params={"user_id": str(target_id)},
        ),
        client.post(
            "/v1/admin/subscriptions",
            headers=_bearer(token),
            json={
                "user_id": str(target_id),
                "scopes": [{"jurisdiction": "uk", "sector": "banking"}],
            },
        ),
        client.patch(
            f"/v1/admin/subscriptions/{uuid.uuid4()}",
            headers=_bearer(token),
            json={"add_scopes": [{"jurisdiction": "uk", "sector": "fintech"}]},
        ),
    ):
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "admin role required"


# ---- 4. Audit row count -----------------------------------------------------


@pytest.mark.integration
def test_admin_write_creates_one_audit_row_per_request(
    client: TestClient,
    migrated_postgres_a: Engine,
) -> None:
    admin_id = _make_user(migrated_postgres_a, "admin_audit@example.com", role="admin")
    target_id = _make_user(migrated_postgres_a, "client_audit@example.com", role="client")

    before = _audit_count_for(migrated_postgres_a, admin_id)
    admin_token = _login(client, "admin_audit@example.com")

    create = client.post(
        "/v1/admin/subscriptions",
        headers=_bearer(admin_token),
        json={
            "user_id": str(target_id),
            "scopes": [{"jurisdiction": "uk", "sector": "banking"}],
        },
    )
    assert create.status_code == 201, create.text
    sub_id = create.json()["id"]

    after_post = _audit_count_for(migrated_postgres_a, admin_id)
    assert after_post == before + 1, "POST must write exactly one audit row"

    patch = client.patch(
        f"/v1/admin/subscriptions/{sub_id}",
        headers=_bearer(admin_token),
        json={"add_scopes": [{"jurisdiction": "uk", "sector": "fintech"}]},
    )
    assert patch.status_code == 200, patch.text
    after_patch = _audit_count_for(migrated_postgres_a, admin_id)
    assert after_patch == after_post + 1, "PATCH must write exactly one audit row"

    # And the shape is right (mode + target_user_id consistency).
    with migrated_postgres_a.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT mode, target_user_id, reason FROM admin_access_log "
                "WHERE admin_id = :a ORDER BY granted_at"
            ),
            {"a": admin_id},
        ).all()
    assert [r[0] for r in rows] == ["operator", "operator"]
    assert all(r[1] is None for r in rows), "operator rows must have NULL target_user_id"

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Integration tests for ``GET /v1/me/overview`` (WU5.1 / Task 5).

Drives the full FastAPI stack against testcontainers Postgres.

Test cases:
1. UK client sees ``is_admin=false``, ``UK`` subscribed, ``BANKING``
   subscribed; all corpus jurisdictions appear with ``subscribed=false``
   for the rest.
2. EU client sees ``EU`` with ``subscribed=true, document_count=2``
   and ``BANKING`` sector ``subscribed=true``.
3. Admin sees ``is_admin=true``, every entry ``subscribed=true``,
   totals = total corpus counts.
4. ``Cache-Control`` header contains ``no-store``.
5. ``jurisdictions`` and ``sectors`` lists are sorted by ``code``
   ascending.

Data setup uses an inline seed fixture (the curated-set script is
CLI-only and pulls from disk fixtures + env var; easier to seed directly
into the shared testcontainers Postgres). Corpus shape:

  UK, BANKING        1
  EU, BANKING        2   (two rows so document_count=2 is exercised)
  IE, corporate-governance  1
  BE, employment     1
  AT, BANKING        1
  DE, employment     1
  IT, BANKING        1
  ES, tax            1
  DK, BANKING        1
"""

from __future__ import annotations

import hashlib
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


REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

ISSUER = "horizons-api-overview-test"
AUDIENCE = "horizons-clients-overview-test"


# ---- Postgres + Alembic (session-scoped, shared with primitives tests) ------


@pytest.fixture(scope="session")
def migrated_postgres_ov(postgres_container: PostgresContainer) -> Iterator[Engine]:
    """Session-scoped migrated sync engine for overview tests."""
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    import os

    cfg = Config(str(ALEMBIC_INI))
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


# ---- RSA keypair + env -------------------------------------------------------


@pytest.fixture(scope="session")
def rsa_pems_ov() -> tuple[bytes, bytes]:
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
def configured_env_ov(
    rsa_pems_ov: tuple[bytes, bytes],
    migrated_postgres_ov: Engine,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, bytes]:
    _ = migrated_postgres_ov
    private_pem, public_pem = rsa_pems_ov
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
def client_ov(configured_env_ov: tuple[bytes, bytes]) -> Iterator[TestClient]:
    _ = configured_env_ov
    from horizons_api.app import create_app

    app = create_app()
    with TestClient(app, base_url="https://testserver") as c:
        yield c


# ---- Seed helpers ------------------------------------------------------------


def _sha256() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _seed_doc_ov(
    engine: Engine,
    *,
    jurisdiction: str,
    sector: str,
    label: str,
) -> uuid.UUID:
    """Insert one document row and return its id."""
    with engine.begin() as conn:
        doc_id: uuid.UUID = conn.execute(
            text(
                "INSERT INTO documents "
                "(jurisdiction, sector, lawstronaut_document_id, title) "
                "VALUES (:j, :s, :lid, :t) RETURNING id"
            ),
            {
                "j": jurisdiction,
                "s": sector,
                "lid": f"ov_{label}_{uuid.uuid4()}",
                "t": f"ov_{label}",
            },
        ).scalar_one()
    return doc_id


def _seed_change_event_ov(
    engine: Engine,
    *,
    document_id: uuid.UUID,
    jurisdiction: str,
    sector: str,
) -> None:
    """Insert one change_events row pinned to an existing document.

    Creates the document_version row required by the FK on the fly so
    the test does not depend on the ingestion-worker code path.
    """
    with engine.begin() as conn:
        version_id = conn.execute(
            text(
                "INSERT INTO document_versions "
                "(document_id, version_label, content_sha256, content_bytes, "
                "content_blob_container, content_blob_key) "
                "VALUES (:d, :v, :h, :b, :c, :k) RETURNING id"
            ),
            {
                "d": document_id,
                "v": f"v_{uuid.uuid4().hex[:6]}",
                "h": _sha256(),
                "b": 1,
                "c": "test",
                "k": f"k/{uuid.uuid4()}",
            },
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO change_events "
                "(document_id, document_version_id, jurisdiction, sector, "
                "change_type, alignment_confidence) "
                "VALUES (:d, :v, :j, :s, 'MODIFIED', 0.95)"
            ),
            {"d": document_id, "v": version_id, "j": jurisdiction, "s": sector},
        )


def _seed_user_ov(
    engine: Engine,
    email: str,
    *,
    scope: tuple[tuple[str, str], ...],
    role: str = "client",
) -> uuid.UUID:
    """Insert a user with an active subscription covering ``scope``."""
    pw_hash = hash_password("pw")
    with engine.begin() as conn:
        uid: uuid.UUID = conn.execute(
            text("INSERT INTO users (email, password_hash, role) VALUES (:e, :p, :r) RETURNING id"),
            {"e": email, "p": pw_hash, "r": role},
        ).scalar_one()
        if scope:
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
                        "INSERT INTO subscription_scopes "
                        "(subscription_id, jurisdiction, sector) "
                        "VALUES (:s, :j, :sec)"
                    ),
                    {"s": sub, "j": j, "sec": s},
                )
    return uid


def _login_ov(client: TestClient, email: str) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "pw"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer_ov(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- Shared corpus fixture ---------------------------------------------------

# Each test function that writes to the DB must use a unique email to avoid
# uniqueness conflicts in the session-scoped Postgres container.  The corpus
# documents are inserted once into the shared container; we use unique
# lawstronaut_document_id values (via uuid4) so idempotency is not a concern.


@pytest.fixture(scope="session")
def seeded_curated_set(migrated_postgres_ov: Engine) -> None:
    """Seed the corpus with a known (jurisdiction, sector) shape.

    Shape (document count):
      UK, BANKING              1
      EU, BANKING              2   ← two rows; doc_count=2 is exercised
      IE, corporate-governance 1
      BE, employment           1
      AT, BANKING              1
      DE, employment           1
      IT, BANKING              1
      ES, tax                  1
      DK, BANKING              1

    Total: 10 documents, 8 jurisdictions, 5 sectors.
    """
    rows: list[tuple[str, str]] = [
        ("UK", "BANKING"),
        ("EU", "BANKING"),
        ("EU", "BANKING"),  # second EU/BANKING doc → document_count=2
        ("IE", "corporate-governance"),
        ("BE", "employment"),
        ("AT", "BANKING"),
        ("DE", "employment"),
        ("IT", "BANKING"),
        ("ES", "tax"),
        ("DK", "BANKING"),
    ]
    for i, (j, s) in enumerate(rows):
        _seed_doc_ov(migrated_postgres_ov, jurisdiction=j, sector=s, label=f"seed{i}")


# ---- Tests -------------------------------------------------------------------


@pytest.mark.integration
def test_overview_uk_client_subscribed_flags(
    client_ov: TestClient,
    migrated_postgres_ov: Engine,
    seeded_curated_set: None,
) -> None:
    """UK client with BANKING subscription sees UK+BANKING subscribed, rest not."""
    _seed_user_ov(
        migrated_postgres_ov,
        "ov_uk@example.com",
        scope=(("UK", "BANKING"),),
    )
    token = _login_ov(client_ov, "ov_uk@example.com")
    response = client_ov.get("/v1/me/overview", headers=_bearer_ov(token))

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["is_admin"] is False

    # Subscribed jurisdiction
    juris_by_code = {j["code"]: j for j in body["jurisdictions"]}
    assert "UK" in juris_by_code, "UK must appear in corpus"
    assert juris_by_code["UK"]["subscribed"] is True

    # All other jurisdictions present but not subscribed
    for code, item in juris_by_code.items():
        if code != "UK":
            assert item["subscribed"] is False, f"{code} should not be subscribed"

    # Subscribed sector
    sector_by_code = {s["code"]: s for s in body["sectors"]}
    assert "BANKING" in sector_by_code
    assert sector_by_code["BANKING"]["subscribed"] is True

    for code, item in sector_by_code.items():
        if code != "BANKING":
            assert item["subscribed"] is False, f"sector {code} should not be subscribed"

    # All 8 corpus jurisdictions appear
    assert len(juris_by_code) >= 8


@pytest.mark.integration
def test_overview_eu_client_document_count(
    client_ov: TestClient,
    migrated_postgres_ov: Engine,
    seeded_curated_set: None,
) -> None:
    """EU client sees EU jurisdiction with document_count=2 and BANKING subscribed."""
    _seed_user_ov(
        migrated_postgres_ov,
        "ov_eu@example.com",
        scope=(("EU", "BANKING"),),
    )
    token = _login_ov(client_ov, "ov_eu@example.com")
    response = client_ov.get("/v1/me/overview", headers=_bearer_ov(token))

    assert response.status_code == 200, response.text
    body = response.json()

    juris_by_code = {j["code"]: j for j in body["jurisdictions"]}
    assert "EU" in juris_by_code
    assert juris_by_code["EU"]["subscribed"] is True
    assert juris_by_code["EU"]["document_count"] == 2

    sector_by_code = {s["code"]: s for s in body["sectors"]}
    assert sector_by_code["BANKING"]["subscribed"] is True


@pytest.mark.integration
def test_overview_admin_sees_all_subscribed(
    client_ov: TestClient,
    migrated_postgres_ov: Engine,
    seeded_curated_set: None,
) -> None:
    """Admin sees is_admin=true, every entry subscribed=true, totals = corpus counts."""
    _seed_user_ov(
        migrated_postgres_ov,
        "ov_admin@example.com",
        scope=(),
        role="admin",
    )
    token = _login_ov(client_ov, "ov_admin@example.com")
    response = client_ov.get("/v1/me/overview", headers=_bearer_ov(token))

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["is_admin"] is True

    for item in body["jurisdictions"]:
        assert item["subscribed"] is True, (
            f"jurisdiction {item['code']} should be subscribed for admin"
        )

    for item in body["sectors"]:
        assert item["subscribed"] is True, f"sector {item['code']} should be subscribed for admin"

    totals = body["totals"]
    assert totals["subscribed_jurisdictions"] == totals["jurisdictions"]
    assert totals["subscribed_sectors"] == totals["sectors"]
    assert totals["documents"] >= 10


@pytest.mark.integration
def test_overview_cache_control_no_store(
    client_ov: TestClient,
    migrated_postgres_ov: Engine,
    seeded_curated_set: None,
) -> None:
    """Response carries Cache-Control: private, no-store."""
    _seed_user_ov(
        migrated_postgres_ov,
        "ov_cache@example.com",
        scope=(("UK", "BANKING"),),
    )
    token = _login_ov(client_ov, "ov_cache@example.com")
    response = client_ov.get("/v1/me/overview", headers=_bearer_ov(token))

    assert response.status_code == 200, response.text
    cache_header = response.headers.get("Cache-Control", "")
    assert "no-store" in cache_header, f"expected no-store in Cache-Control, got: {cache_header!r}"


@pytest.mark.integration
def test_overview_change_count_rolls_up(
    client_ov: TestClient,
    migrated_postgres_ov: Engine,
    seeded_curated_set: None,
) -> None:
    """change_count rolls up per jurisdiction and sector; defaults to 0."""
    doc_id = _seed_doc_ov(
        migrated_postgres_ov,
        jurisdiction="FR",
        sector="BANKING",
        label="changecount",
    )
    _seed_change_event_ov(
        migrated_postgres_ov,
        document_id=doc_id,
        jurisdiction="FR",
        sector="BANKING",
    )
    _seed_change_event_ov(
        migrated_postgres_ov,
        document_id=doc_id,
        jurisdiction="FR",
        sector="BANKING",
    )

    _seed_user_ov(
        migrated_postgres_ov,
        "ov_changecount@example.com",
        scope=(),
        role="admin",
    )
    token = _login_ov(client_ov, "ov_changecount@example.com")
    response = client_ov.get("/v1/me/overview", headers=_bearer_ov(token))

    assert response.status_code == 200, response.text
    body = response.json()

    juris_by_code = {j["code"]: j for j in body["jurisdictions"]}
    assert "change_count" in juris_by_code["FR"], "change_count must be present on every entry"
    assert juris_by_code["FR"]["change_count"] >= 2

    # Jurisdictions with no recorded changes report 0, not missing.
    for code, item in juris_by_code.items():
        assert isinstance(item["change_count"], int), f"{code} change_count must be int"

    sector_by_code = {s["code"]: s for s in body["sectors"]}
    assert sector_by_code["BANKING"]["change_count"] >= 2


@pytest.mark.integration
def test_overview_lists_sorted_by_code(
    client_ov: TestClient,
    migrated_postgres_ov: Engine,
    seeded_curated_set: None,
) -> None:
    """jurisdictions and sectors lists are sorted by code ascending."""
    _seed_user_ov(
        migrated_postgres_ov,
        "ov_sort@example.com",
        scope=(("UK", "BANKING"),),
    )
    token = _login_ov(client_ov, "ov_sort@example.com")
    response = client_ov.get("/v1/me/overview", headers=_bearer_ov(token))

    assert response.status_code == 200, response.text
    body = response.json()

    juris_codes = [j["code"] for j in body["jurisdictions"]]
    assert juris_codes == sorted(juris_codes), f"jurisdictions not sorted: {juris_codes}"

    sector_codes = [s["code"] for s in body["sectors"]]
    assert sector_codes == sorted(sector_codes), f"sectors not sorted: {sector_codes}"

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Integration tests for ``/v1/documents`` (WU8.5).

Drives the full FastAPI stack against testcontainers Postgres:

- ``GET /v1/documents`` — list endpoint, scope-filtered for clients,
  full corpus for admin. Filters by jurisdiction / sector / search.
- ``GET /v1/documents/{id}`` — detail + versions array.
- ``GET /v1/documents/{id}/versions/{version_label}/clauses`` — flat
  ordered clause list for the structure-overlay view.

The 404-on-out-of-scope rule mirrors the primitives surface: a client
cannot distinguish "not found" from "not in your subscription scope."
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
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

    from sqlalchemy import Connection, Engine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

ISSUER = "horizons-api-documents-test"
AUDIENCE = "horizons-clients-documents-test"


@pytest.fixture(scope="session")
def migrated_postgres_d(postgres_container: PostgresContainer) -> Iterator[Engine]:
    sync_url = postgres_container.get_connection_url(driver="psycopg")
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
    migrated_postgres_d: Engine,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, bytes]:
    _ = migrated_postgres_d
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


# ---- seed helpers --------------------------------------------------------


def _sha256() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _seed_user(
    engine: Engine,
    email: str,
    *,
    role: str = "client",
    scope: tuple[tuple[str, str], ...] = (("UK", "BANKING"),),
) -> uuid.UUID:
    pw_hash = hash_password("pw")
    with engine.begin() as conn:
        uid = conn.execute(
            text("INSERT INTO users (email, password_hash, role) VALUES (:e, :p, :r) RETURNING id"),
            {"e": email, "p": pw_hash, "r": role},
        ).scalar_one()
        if role != "admin":
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


def _seed_doc_with_version(
    engine: Engine,
    *,
    jurisdiction: str,
    sector: str,
    title: str,
    version_label: str = "v1",
) -> tuple[uuid.UUID, uuid.UUID]:
    with engine.begin() as conn:
        return _make_doc(
            conn,
            jurisdiction=jurisdiction,
            sector=sector,
            title=title,
            version_label=version_label,
        )


def _make_doc(
    conn: Connection,
    *,
    jurisdiction: str,
    sector: str,
    title: str,
    version_label: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    doc_id = conn.execute(
        text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "lid": f"doc_{uuid.uuid4()}",
            "t": title,
        },
    ).scalar_one()
    ver_id = conn.execute(
        text(
            "INSERT INTO document_versions "
            "(document_id, version_label, publication_date, effective_date, "
            "content_blob_container, content_blob_key, content_sha256, "
            "content_bytes) "
            "VALUES (:d, :lab, :p, :e, 'docs', :k, :h, 1024) RETURNING id"
        ),
        {
            "d": doc_id,
            "lab": version_label,
            "p": datetime.now(UTC),
            "e": datetime.now(UTC),
            "k": f"{doc_id}/{version_label}.md",
            "h": _sha256(),
        },
    ).scalar_one()
    return doc_id, ver_id


def _insert_clause(
    engine: Engine,
    version_id: uuid.UUID,
    path: str,
    ord_value: int,
    text_content: str,
) -> uuid.UUID:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO clauses "
                "(document_version_id, clause_uid, clause_path, text_content, ord) "
                "VALUES (:v, :u, :p, :t, :o) RETURNING id"
            ),
            {
                "v": version_id,
                "u": uuid.uuid4(),
                "p": path,
                "t": text_content,
                "o": ord_value,
            },
        ).scalar_one()


def _login(c: TestClient, email: str) -> str:
    response = c.post("/v1/auth/login", json={"email": email, "password": "pw"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- tests ---------------------------------------------------------------


@pytest.mark.integration
def test_documents_list_requires_bearer(
    client: TestClient,
    migrated_postgres_d: Engine,
) -> None:
    _ = migrated_postgres_d
    assert client.get("/v1/documents").status_code == 401


@pytest.mark.integration
def test_documents_list_returns_in_scope_with_cache_header(
    client: TestClient,
    migrated_postgres_d: Engine,
) -> None:
    _seed_user(migrated_postgres_d, "uk_lister@example.com", scope=(("UK", "BANKING"),))
    in_doc, _ = _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="UK", sector="BANKING", title="UK doc in scope"
    )
    _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="EU", sector="BANKING", title="EU doc out of scope"
    )

    token = _login(client, "uk_lister@example.com")
    response = client.get("/v1/documents", headers=_bearer(token))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    ids = [item["id"] for item in body["items"]]
    assert str(in_doc) in ids
    for item in body["items"]:
        assert item["jurisdiction"] == "UK"
        assert item["sector"] == "BANKING"


@pytest.mark.integration
def test_documents_list_filter_by_jurisdiction_and_search(
    client: TestClient,
    migrated_postgres_d: Engine,
) -> None:
    _seed_user(
        migrated_postgres_d,
        "filter_user@example.com",
        scope=(("UK", "BANKING"), ("EU", "BANKING")),
    )
    _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="UK", sector="BANKING", title="needle in haystack"
    )
    _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="EU", sector="BANKING", title="needle elsewhere"
    )
    _seed_doc_with_version(migrated_postgres_d, jurisdiction="UK", sector="BANKING", title="other")

    token = _login(client, "filter_user@example.com")

    # Jurisdiction filter narrows to UK only.
    uk_resp = client.get("/v1/documents", params={"jurisdiction": "UK"}, headers=_bearer(token))
    assert uk_resp.status_code == 200
    for item in uk_resp.json()["items"]:
        assert item["jurisdiction"] == "UK"

    # Search filters by title substring across scope.
    needle_resp = client.get("/v1/documents", params={"search": "needle"}, headers=_bearer(token))
    assert needle_resp.status_code == 200
    titles = [it["title"] for it in needle_resp.json()["items"]]
    assert all("needle" in t for t in titles)
    assert len(titles) >= 2


@pytest.mark.integration
def test_documents_list_admin_sees_full_corpus(
    client: TestClient,
    migrated_postgres_d: Engine,
) -> None:
    _seed_user(migrated_postgres_d, "doc_admin@example.com", role="admin")
    _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="UK", sector="BANKING", title="admin sees UK"
    )
    _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="JP", sector="INSURANCE", title="admin sees JP"
    )

    token = _login(client, "doc_admin@example.com")
    response = client.get("/v1/documents", headers=_bearer(token))

    assert response.status_code == 200
    titles = [it["title"] for it in response.json()["items"]]
    assert "admin sees UK" in titles
    assert "admin sees JP" in titles


@pytest.mark.integration
def test_document_detail_returns_versions(
    client: TestClient,
    migrated_postgres_d: Engine,
) -> None:
    _seed_user(migrated_postgres_d, "det_user@example.com", scope=(("UK", "BANKING"),))
    doc_id, _ = _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="UK", sector="BANKING", title="detail target"
    )

    token = _login(client, "det_user@example.com")
    response = client.get(f"/v1/documents/{doc_id}", headers=_bearer(token))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(doc_id)
    assert body["title"] == "detail target"
    assert len(body["versions"]) == 1
    assert body["versions"][0]["version_label"] == "v1"


@pytest.mark.integration
def test_document_detail_out_of_scope_returns_404(
    client: TestClient,
    migrated_postgres_d: Engine,
) -> None:
    _seed_user(migrated_postgres_d, "uk_404@example.com", scope=(("UK", "BANKING"),))
    eu_doc, _ = _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="EU", sector="BANKING", title="EU only"
    )

    token = _login(client, "uk_404@example.com")
    response = client.get(f"/v1/documents/{eu_doc}", headers=_bearer(token))
    assert response.status_code == 404


@pytest.mark.integration
def test_get_clauses_returns_ordered_list(
    client: TestClient,
    migrated_postgres_d: Engine,
) -> None:
    _seed_user(migrated_postgres_d, "clauses_user@example.com", scope=(("UK", "BANKING"),))
    doc_id, ver_id = _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="UK", sector="BANKING", title="with clauses"
    )
    # Insert out of order; the API must sort by ``ord``.
    _insert_clause(migrated_postgres_d, ver_id, "PART_1/SECTION_2", 2, "second clause")
    _insert_clause(migrated_postgres_d, ver_id, "PART_1/SECTION_1", 1, "first clause")
    _insert_clause(migrated_postgres_d, ver_id, "PART_1/SECTION_3/(a)", 3, "deeper clause")

    token = _login(client, "clauses_user@example.com")
    response = client.get(f"/v1/documents/{doc_id}/versions/v1/clauses", headers=_bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert body["version_label"] == "v1"
    assert [c["clause_path"] for c in body["clauses"]] == [
        "PART_1/SECTION_1",
        "PART_1/SECTION_2",
        "PART_1/SECTION_3/(a)",
    ]
    assert body["clauses"][0]["text_content"] == "first clause"
    # heading_text appears on every clause (null for body-only rows).
    assert body["clauses"][0]["heading_text"] is None


@pytest.mark.integration
def test_get_clauses_out_of_scope_returns_404(
    client: TestClient,
    migrated_postgres_d: Engine,
) -> None:
    _seed_user(migrated_postgres_d, "uk_clauses_404@example.com", scope=(("UK", "BANKING"),))
    eu_doc, eu_ver = _seed_doc_with_version(
        migrated_postgres_d, jurisdiction="EU", sector="BANKING", title="EU clauses"
    )
    _insert_clause(migrated_postgres_d, eu_ver, "PART_1/SECTION_1", 1, "out of scope")

    token = _login(client, "uk_clauses_404@example.com")
    response = client.get(f"/v1/documents/{eu_doc}/versions/v1/clauses", headers=_bearer(token))
    assert response.status_code == 404

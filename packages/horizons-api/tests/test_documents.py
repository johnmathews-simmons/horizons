# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Integration tests for ``/v1/documents`` — clause and change-count fields.

The list + detail handlers now surface per-document aggregates
(``clause_count``, ``change_counts``, ``previous_version_at``,
``current_version_at``) computed by the repo from the latest two
versions. These tests seed documents, versions, clauses, and
change_events directly via the migrated superuser engine (the
``admin_bypass`` role used by the API is SELECT-only on corpus
tables) and assert the wire shape.

Patterned on ``test_overview.py``: same TestClient setup, same
``_seed_user_ov`` / ``_login_ov`` / ``_bearer_ov`` rhythm.
"""

from __future__ import annotations

import hashlib
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


REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

ISSUER = "horizons-api-documents-test"
AUDIENCE = "horizons-clients-documents-test"


# ---- Postgres + Alembic (session-scoped) -------------------------------------


@pytest.fixture(scope="session")
def migrated_postgres_docs(postgres_container: PostgresContainer) -> Iterator[Engine]:
    """Session-scoped migrated sync engine for documents tests."""
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


# ---- RSA keypair + configured env --------------------------------------------


@pytest.fixture(scope="session")
def rsa_pems_docs() -> tuple[bytes, bytes]:
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
def configured_env_docs(
    rsa_pems_docs: tuple[bytes, bytes],
    migrated_postgres_docs: Engine,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, bytes]:
    _ = migrated_postgres_docs
    private_pem, public_pem = rsa_pems_docs
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
def client_docs(configured_env_docs: tuple[bytes, bytes]) -> Iterator[TestClient]:
    _ = configured_env_docs
    from horizons_api.app import create_app

    app = create_app()
    with TestClient(app, base_url="https://testserver") as c:
        yield c


# ---- Seed helpers ------------------------------------------------------------


def _sha256() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _insert_document(conn: Connection, *, jurisdiction: str, sector: str, title: str) -> uuid.UUID:
    return conn.execute(
        text(
            """
            INSERT INTO documents (jurisdiction, sector, lawstronaut_document_id, title)
            VALUES (:j, :s, :ldid, :t)
            RETURNING id
            """
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "ldid": f"ldid-{uuid.uuid4().hex[:12]}",
            "t": title,
        },
    ).scalar_one()


def _insert_version(
    conn: Connection,
    *,
    document_id: uuid.UUID,
    label: str,
    effective_date: datetime,
    clause_count: int,
) -> uuid.UUID:
    version_id = conn.execute(
        text(
            """
            INSERT INTO document_versions (
                document_id, version_label, effective_date,
                content_blob_container, content_blob_key, content_sha256, content_bytes
            )
            VALUES (:did, :lbl, :eff, 'ce', :k, :h, 1024)
            RETURNING id
            """
        ),
        {
            "did": document_id,
            "lbl": label,
            "eff": effective_date,
            "k": f"k-{uuid.uuid4().hex}",
            "h": _sha256(),
        },
    ).scalar_one()
    for ord_ in range(clause_count):
        conn.execute(
            text(
                """
                INSERT INTO clauses (
                    document_version_id, clause_uid, clause_path, text_content, ord
                )
                VALUES (:vid, :uid, :path, 'body', :ord)
                """
            ),
            {
                "vid": version_id,
                "uid": uuid.uuid4(),
                "path": f"/{ord_}",
                "ord": ord_,
            },
        )
    return version_id


def _insert_change_event(
    conn: Connection,
    *,
    document_id: uuid.UUID,
    document_version_id: uuid.UUID,
    jurisdiction: str,
    sector: str,
    change_type: str,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO change_events
                (document_id, document_version_id, jurisdiction, sector,
                 change_type, alignment_confidence, detected_at)
            VALUES (:did, :vid, :j, :s, :ct, 0.99, NOW())
            """
        ),
        {
            "did": document_id,
            "vid": document_version_id,
            "j": jurisdiction,
            "s": sector,
            "ct": change_type,
        },
    )


def _seed_user_docs(
    engine: Engine,
    email: str,
    *,
    role: str = "admin",
) -> uuid.UUID:
    """Seed an admin user — no subscription required to read corpus."""
    pw_hash = hash_password("pw")
    with engine.begin() as conn:
        uid: uuid.UUID = conn.execute(
            text("INSERT INTO users (email, password_hash, role) VALUES (:e, :p, :r) RETURNING id"),
            {"e": email, "p": pw_hash, "r": role},
        ).scalar_one()
    return uid


def _login_docs(client: TestClient, email: str) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "pw"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer_docs(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_two_version_doc(
    engine: Engine,
    *,
    jurisdiction: str,
    sector: str = "banking",
    change_counts: dict[str, int],
    clause_count_v2: int,
    clause_count_v1: int = 10,
) -> tuple[uuid.UUID, datetime, datetime]:
    """Seed a document with two versions plus change events on v2.

    Returns ``(document_id, v1_effective_date, v2_effective_date)``.
    """
    v1_eff = datetime(2025, 1, 1, tzinfo=UTC)
    v2_eff = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as conn:
        doc_id = _insert_document(conn, jurisdiction=jurisdiction, sector=sector, title="Test Act")
        _ = _insert_version(
            conn,
            document_id=doc_id,
            label="v1",
            effective_date=v1_eff,
            clause_count=clause_count_v1,
        )
        v2 = _insert_version(
            conn,
            document_id=doc_id,
            label="v2",
            effective_date=v2_eff,
            clause_count=clause_count_v2,
        )
        for change_type, count in change_counts.items():
            for _ in range(count):
                _insert_change_event(
                    conn,
                    document_id=doc_id,
                    document_version_id=v2,
                    jurisdiction=jurisdiction,
                    sector=sector,
                    change_type=change_type,
                )
    return doc_id, v1_eff, v2_eff


def _rand_jurisdiction(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---- Tests -------------------------------------------------------------------


@pytest.mark.integration
def test_list_documents_returns_stats(
    client_docs: TestClient,
    migrated_postgres_docs: Engine,
) -> None:
    """List endpoint surfaces clause_count, change_counts, and version timestamps."""
    jurisdiction = _rand_jurisdiction("UK")
    doc_id, v1_eff, v2_eff = _seed_two_version_doc(
        migrated_postgres_docs,
        jurisdiction=jurisdiction,
        change_counts={"ADDED": 2, "REMOVED": 1, "MODIFIED": 3, "MOVED": 1},
        clause_count_v2=12,
    )

    email = f"docs_list_{uuid.uuid4().hex[:6]}@example.com"
    _seed_user_docs(migrated_postgres_docs, email)
    token = _login_docs(client_docs, email)

    response = client_docs.get(
        f"/v1/documents?jurisdiction={jurisdiction}",
        headers=_bearer_docs(token),
    )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    row = next(it for it in items if it["id"] == str(doc_id))
    assert row["clause_count"] == 12
    assert row["change_counts"] == {
        "added": 2,
        "removed": 1,
        "modified": 3,
        "moved": 1,
    }
    assert row["previous_version_at"].startswith(v1_eff.date().isoformat())
    assert row["current_version_at"].startswith(v2_eff.date().isoformat())


@pytest.mark.integration
def test_detail_returns_same_stats_shape(
    client_docs: TestClient,
    migrated_postgres_docs: Engine,
) -> None:
    """Detail endpoint surfaces the same aggregate shape plus the versions list."""
    jurisdiction = _rand_jurisdiction("UK")
    doc_id, _v1, _v2 = _seed_two_version_doc(
        migrated_postgres_docs,
        jurisdiction=jurisdiction,
        change_counts={"ADDED": 1},
        clause_count_v2=4,
    )

    email = f"docs_detail_{uuid.uuid4().hex[:6]}@example.com"
    _seed_user_docs(migrated_postgres_docs, email)
    token = _login_docs(client_docs, email)

    response = client_docs.get(
        f"/v1/documents/{doc_id}",
        headers=_bearer_docs(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clause_count"] == 4
    assert body["change_counts"]["added"] == 1
    assert body["change_counts"]["removed"] == 0
    assert body["change_counts"]["modified"] == 0
    assert body["change_counts"]["moved"] == 0
    assert "versions" in body
    assert len(body["versions"]) == 2

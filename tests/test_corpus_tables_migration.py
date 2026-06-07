"""Integration test for the WU1.2 corpus tables migration.

Applies the Alembic tree against a fresh Postgres 18 container and
asserts the resulting schema and behaviour:

- Tables, columns, types, NOT NULL.
- Ownership is ``schema_owner``.
- Indexes are present on the documented column tuples.
- ``UNIQUE(document_id, version_label)`` rejects duplicates.
- ``UNIQUE(document_version_id, clause_path)`` rejects duplicates.
- Per-table append-only triggers reject every ``UPDATE``.
- ``uuidv7()`` defaults return UUIDs with version field 0x7.
- Grants: api_app has SELECT only; ingestion_worker has SELECT, INSERT;
  admin_bypass has nothing static.

Sync, like the WU1.1 test — Alembic is a sync API and clashes with the
session-scoped async engine fixture's event loop.
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
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

CORPUS_TABLES = ("documents", "document_versions", "clauses")


@pytest.fixture
def migrated_engine(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> Engine:
    """Apply Alembic head and yield a sync engine pointed at the container."""
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    monkeypatch.setenv("HORIZONS_DB_URL", sync_url)
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    return create_engine(sync_url, future=True)


def _sha256_32() -> bytes:
    return hashlib.sha256(b"hello").digest()


def _insert_document(
    conn: Connection,
    jurisdiction: str = "IE",
    sector: str = "BANKING",
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "lid": f"upstream-{uuid.uuid4()}",
            "t": "Sample Act",
        },
    ).scalar_one()


def _insert_version(
    conn: Connection,
    doc_id: uuid.UUID,
    label: str = "v1",
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO document_versions "
            "(document_id, version_label, publication_date, effective_date, "
            "content_blob_container, content_blob_key, content_sha256, "
            "content_bytes) "
            "VALUES (:d, :l, :p, :e, :c, :k, :h, :b) RETURNING id"
        ),
        {
            "d": doc_id,
            "l": label,
            "p": datetime.now(UTC),
            "e": datetime.now(UTC),
            "c": "corpus",
            "k": f"ie/{doc_id}/{label}.md",
            "h": _sha256_32(),
            "b": 1234,
        },
    ).scalar_one()


def _insert_clause(
    conn: Connection,
    version_id: uuid.UUID,
    path: str = "Part 1 / Section 1",
    ord_value: int = 1,
    clause_uid: uuid.UUID | None = None,
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO clauses "
            "(document_version_id, clause_uid, clause_path, text_content, ord) "
            "VALUES (:v, :u, :p, :t, :o) RETURNING id"
        ),
        {
            "v": version_id,
            "u": clause_uid or uuid.uuid4(),
            "p": path,
            "t": "The Minister shall, by order, prescribe...",
            "o": ord_value,
        },
    ).scalar_one()


@pytest.mark.integration
def test_corpus_tables_exist_with_expected_columns(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.connect() as conn:
            cols = {
                (row.table_name, row.column_name): (
                    row.data_type,
                    row.is_nullable,
                )
                for row in conn.execute(
                    text(
                        """
                        SELECT table_name, column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name IN (
                              'documents', 'document_versions', 'clauses'
                          )
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    # documents
    assert cols[("documents", "id")] == ("uuid", "NO")
    assert cols[("documents", "jurisdiction")] == ("text", "NO")
    assert cols[("documents", "sector")] == ("text", "NO")
    assert cols[("documents", "lawstronaut_document_id")] == ("text", "NO")
    assert cols[("documents", "title")] == ("text", "NO")
    assert cols[("documents", "created_at")] == ("timestamp with time zone", "NO")

    # document_versions
    assert cols[("document_versions", "id")] == ("uuid", "NO")
    assert cols[("document_versions", "document_id")] == ("uuid", "NO")
    assert cols[("document_versions", "version_label")] == ("text", "NO")
    assert cols[("document_versions", "publication_date")] == (
        "timestamp with time zone",
        "YES",
    )
    assert cols[("document_versions", "effective_date")] == (
        "timestamp with time zone",
        "YES",
    )
    assert cols[("document_versions", "content_blob_container")] == ("text", "NO")
    assert cols[("document_versions", "content_blob_key")] == ("text", "NO")
    assert cols[("document_versions", "content_sha256")] == ("bytea", "NO")
    assert cols[("document_versions", "content_bytes")] == ("integer", "NO")
    assert cols[("document_versions", "created_at")] == (
        "timestamp with time zone",
        "NO",
    )

    # clauses
    assert cols[("clauses", "id")] == ("uuid", "NO")
    assert cols[("clauses", "document_version_id")] == ("uuid", "NO")
    assert cols[("clauses", "clause_uid")] == ("uuid", "NO")
    assert cols[("clauses", "clause_path")] == ("text", "NO")
    assert cols[("clauses", "text_content")] == ("text", "NO")
    assert cols[("clauses", "heading_text")] == ("text", "YES")
    assert cols[("clauses", "ord")] == ("integer", "NO")


@pytest.mark.integration
def test_corpus_tables_owned_by_schema_owner(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.connect() as conn:
            owners = {
                row.relname: row.owner
                for row in conn.execute(
                    text(
                        """
                        SELECT c.relname, pg_get_userbyid(c.relowner) AS owner
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relname IN (
                              'documents', 'document_versions', 'clauses'
                          )
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert owners == {name: "schema_owner" for name in CORPUS_TABLES}


@pytest.mark.integration
def test_expected_indexes_present(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            indexes = {
                row.indexname
                for row in conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename IN ('documents', 'document_versions', 'clauses')"
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert "idx_documents_jurisdiction_sector" in indexes
    assert "idx_document_versions_doc_effective" in indexes
    assert "idx_clauses_version_ord" in indexes
    assert "idx_clauses_clause_uid" in indexes


@pytest.mark.integration
def test_documents_uuidv7_default(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            v_id = _insert_version(conn, doc_id)
            c_id = _insert_clause(conn, v_id)
    finally:
        migrated_engine.dispose()

    for value in (doc_id, v_id, c_id):
        assert isinstance(value, uuid.UUID)
        assert value.version == 7


@pytest.mark.integration
def test_document_versions_unique_label_rejects_duplicate(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            _insert_version(conn, doc_id, label="v1")
        with (
            pytest.raises(IntegrityError),
            migrated_engine.begin() as conn,
        ):
            _insert_version(conn, doc_id, label="v1")
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_clauses_unique_path_per_version_rejects_duplicate(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            v_id = _insert_version(conn, doc_id)
            _insert_clause(conn, v_id, path="Part 1 / Section 1", ord_value=1)
        with (
            pytest.raises(IntegrityError),
            migrated_engine.begin() as conn,
        ):
            _insert_clause(conn, v_id, path="Part 1 / Section 1", ord_value=2)
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_documents_reject_update(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text("UPDATE documents SET title = 'Renamed' WHERE id = :id"),
                {"id": doc_id},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_document_versions_reject_update(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            v_id = _insert_version(conn, doc_id)
        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text("UPDATE document_versions SET content_bytes = 0 WHERE id = :id"),
                {"id": v_id},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_clauses_reject_update(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            v_id = _insert_version(conn, doc_id)
            c_id = _insert_clause(conn, v_id)
        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text("UPDATE clauses SET text_content = 'changed' WHERE id = :id"),
                {"id": c_id},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_per_role_grants_match_design(migrated_engine: Engine) -> None:
    """api_app has SELECT only; ingestion_worker has SELECT+INSERT on
    documents and clauses; SELECT+INSERT+UPDATE on document_versions
    (UPDATE column-scoped to ``valid_to`` by WU3.1 — see the dedicated
    column-grant test in ``test_ingestion_tables_migration``).
    admin_bypass has SELECT only (added by WU1.4 so BYPASSRLS reads
    are functional — BYPASSRLS does not override table-level GRANTs).
    """
    try:
        with migrated_engine.connect() as conn:
            rows = list(
                conn.execute(
                    text(
                        """
                        SELECT grantee, table_name, privilege_type
                        FROM information_schema.role_table_grants
                        WHERE table_schema = 'public'
                          AND table_name IN (
                              'documents', 'document_versions', 'clauses'
                          )
                          AND grantee IN (
                              'api_app', 'ingestion_worker', 'admin_bypass'
                          )
                        """
                    )
                )
            )
    finally:
        migrated_engine.dispose()

    grants: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        grants.setdefault((row.grantee, row.table_name), set()).add(row.privilege_type)

    for table in CORPUS_TABLES:
        assert grants.get(("api_app", table)) == {"SELECT"}
        assert grants.get(("admin_bypass", table)) == {"SELECT"}
        # ingestion_worker's UPDATE on document_versions is column-scoped
        # to ``valid_to`` (added by WU3.1) and lives in
        # information_schema.column_privileges, not role_table_grants.
        # The dedicated column-grant assertion lives in
        # test_ingestion_tables_migration.
        assert grants.get(("ingestion_worker", table)) == {"SELECT", "INSERT"}


@pytest.mark.integration
def test_documents_lawstronaut_id_unique(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.begin() as conn:
            shared = f"shared-{uuid.uuid4()}"
            conn.execute(
                text(
                    "INSERT INTO documents "
                    "(jurisdiction, sector, lawstronaut_document_id, title) "
                    "VALUES ('IE', 'BANKING', :lid, 'A')"
                ),
                {"lid": shared},
            )
        with (
            pytest.raises(IntegrityError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text(
                    "INSERT INTO documents "
                    "(jurisdiction, sector, lawstronaut_document_id, title) "
                    "VALUES ('UK', 'INSURANCE', :lid, 'B')"
                ),
                {"lid": shared},
            )
    finally:
        migrated_engine.dispose()

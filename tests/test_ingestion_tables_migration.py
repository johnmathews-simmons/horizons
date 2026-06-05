"""Integration test for the WU3.1 ingestion tables migration.

Applies the Alembic tree against a fresh Postgres 18 container and
asserts the resulting schema and behaviour after ``0007_ingestion_tables``:

- ``document_versions`` gains nullable ``version_no``, ``valid_from``,
  ``valid_to``; the append-only trigger now permits ``UPDATE`` iff
  ``valid_to`` is the only column that changed.
- ``document_poll_schedule`` exists with ``document_id`` as PK and the
  shape required by the SKIP LOCKED claim loop from ADR-0001.
- ``ingestion_incident`` exists as an append-only log keyed by
  ``bigserial id`` and FK'd to ``documents``.
- Indexes are present per access pattern.
- Grants: ``ingestion_worker`` has SELECT/INSERT on documents +
  ingestion_incident, SELECT/INSERT/UPDATE on document_poll_schedule,
  and SELECT/INSERT/UPDATE on document_versions (UPDATE column-scoped
  to ``valid_to``).
- ``api_app`` has zero grants on the two new tables.
- ``UNIQUE(document_id, version_no)`` rejects duplicates.

Sync, like the other migration tests — Alembic is a sync API and
clashes with the session-scoped async engine fixture's event loop.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
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

NEW_TABLES = ("document_poll_schedule", "ingestion_incident")


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
    version_no: int | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO document_versions "
            "(document_id, version_label, version_no, valid_from, valid_to, "
            "publication_date, effective_date, content_blob_container, "
            "content_blob_key, content_sha256, content_bytes) "
            "VALUES (:d, :l, :vn, :vf, :vt, :p, :e, :c, :k, :h, :b) "
            "RETURNING id"
        ),
        {
            "d": doc_id,
            "l": label,
            "vn": version_no,
            "vf": valid_from,
            "vt": valid_to,
            "p": datetime.now(UTC),
            "e": datetime.now(UTC),
            "c": "corpus",
            "k": f"ie/{doc_id}/{label}.md",
            "h": _sha256_32(),
            "b": 1234,
        },
    ).scalar_one()


@pytest.mark.integration
def test_document_versions_gains_ingestion_columns(
    migrated_engine: Engine,
) -> None:
    """``version_no`` / ``valid_from`` / ``valid_to`` added as nullable."""
    try:
        with migrated_engine.connect() as conn:
            cols = {
                row.column_name: (row.data_type, row.is_nullable)
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'document_versions'
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert cols["version_no"] == ("integer", "YES")
    assert cols["valid_from"] == ("timestamp with time zone", "YES")
    assert cols["valid_to"] == ("timestamp with time zone", "YES")


@pytest.mark.integration
def test_new_tables_exist_with_expected_columns(
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
                              'document_poll_schedule', 'ingestion_incident'
                          )
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    # document_poll_schedule
    assert cols[("document_poll_schedule", "document_id")] == ("uuid", "NO")
    assert cols[("document_poll_schedule", "cadence_interval")] == ("interval", "NO")
    assert cols[("document_poll_schedule", "next_poll_at")] == (
        "timestamp with time zone",
        "NO",
    )
    assert cols[("document_poll_schedule", "last_polled_at")] == (
        "timestamp with time zone",
        "YES",
    )
    assert cols[("document_poll_schedule", "failure_count")] == ("integer", "NO")

    # ingestion_incident — bigint id, FK to documents.
    assert cols[("ingestion_incident", "id")] == ("bigint", "NO")
    assert cols[("ingestion_incident", "document_id")] == ("uuid", "NO")
    assert cols[("ingestion_incident", "error_class")] == ("text", "NO")
    assert cols[("ingestion_incident", "payload")] == ("jsonb", "NO")
    assert cols[("ingestion_incident", "occurred_at")] == (
        "timestamp with time zone",
        "NO",
    )


@pytest.mark.integration
def test_new_tables_owned_by_schema_owner(migrated_engine: Engine) -> None:
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
                              'document_poll_schedule', 'ingestion_incident'
                          )
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert owners == {name: "schema_owner" for name in NEW_TABLES}


@pytest.mark.integration
def test_expected_indexes_present(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            indexes = {
                row.indexname
                for row in conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename IN ("
                        "  'document_versions', 'document_poll_schedule', "
                        "  'ingestion_incident'"
                        ")"
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    # WU3.1 additions.
    assert "idx_document_versions_doc_valid_to" in indexes
    assert "idx_document_poll_schedule_next_poll_at" in indexes
    assert "idx_ingestion_incident_doc_occurred" in indexes
    assert "idx_ingestion_incident_occurred_at" in indexes


@pytest.mark.integration
def test_document_poll_schedule_pk_and_fk(migrated_engine: Engine) -> None:
    """document_id is PK + FK; inserting a row for a non-existent doc fails;
    inserting two rows for the same doc fails on PK collision.
    """
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            conn.execute(
                text(
                    "INSERT INTO document_poll_schedule "
                    "(document_id, cadence_interval, next_poll_at) "
                    "VALUES (:d, :c, :n)"
                ),
                {
                    "d": doc_id,
                    "c": timedelta(hours=24),
                    "n": datetime.now(UTC),
                },
            )

        # PK collision on a second row for the same document.
        with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO document_poll_schedule "
                    "(document_id, cadence_interval, next_poll_at) "
                    "VALUES (:d, :c, :n)"
                ),
                {
                    "d": doc_id,
                    "c": timedelta(hours=24),
                    "n": datetime.now(UTC),
                },
            )

        # FK violation for an unknown document.
        with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO document_poll_schedule "
                    "(document_id, cadence_interval, next_poll_at) "
                    "VALUES (:d, :c, :n)"
                ),
                {
                    "d": uuid.uuid4(),
                    "c": timedelta(hours=24),
                    "n": datetime.now(UTC),
                },
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_document_versions_unique_doc_version_no(
    migrated_engine: Engine,
) -> None:
    """``UNIQUE(document_id, version_no)`` rejects duplicates."""
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            _insert_version(conn, doc_id, label="v1", version_no=1)
        with (
            pytest.raises(IntegrityError),
            migrated_engine.begin() as conn,
        ):
            _insert_version(conn, doc_id, label="v1-dup", version_no=1)
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_document_versions_update_valid_to_only_permitted(
    migrated_engine: Engine,
) -> None:
    """The relaxed trigger permits UPDATE iff only ``valid_to`` changes."""
    try:
        # Allowed: extending valid_to in place.
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            t0 = datetime.now(UTC)
            v_id = _insert_version(
                conn,
                doc_id,
                version_no=1,
                valid_from=t0,
                valid_to=t0,
            )
        with migrated_engine.begin() as conn:
            conn.execute(
                text("UPDATE document_versions SET valid_to = :v WHERE id = :id"),
                {"v": t0 + timedelta(minutes=5), "id": v_id},
            )

        # Disallowed: changing any other column (even alongside valid_to).
        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text("UPDATE document_versions SET content_bytes = 0 WHERE id = :id"),
                {"id": v_id},
            )

        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text(
                    "UPDATE document_versions SET valid_to = :v, content_bytes = 0 WHERE id = :id"
                ),
                {"v": t0 + timedelta(minutes=10), "id": v_id},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_per_role_grants_match_design(migrated_engine: Engine) -> None:
    """``ingestion_worker`` has the per-table grants WU3.1 requires;
    ``api_app`` has zero grants on the two new tables."""
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
                              'document_versions',
                              'document_poll_schedule', 'ingestion_incident'
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

    # ingestion_worker — WU3.1 table-level grants. UPDATE on
    # document_versions is column-scoped (valid_to) so it shows in
    # column_privileges, not role_table_grants — see the dedicated
    # ``test_ingestion_worker_update_on_document_versions_is_column_scoped``.
    assert grants.get(("ingestion_worker", "document_versions")) == {
        "SELECT",
        "INSERT",
    }
    assert grants.get(("ingestion_worker", "document_poll_schedule")) == {
        "SELECT",
        "INSERT",
        "UPDATE",
    }
    assert grants.get(("ingestion_worker", "ingestion_incident")) == {
        "SELECT",
        "INSERT",
    }

    # api_app — zero grants on either of the operator-only tables.
    assert grants.get(("api_app", "document_poll_schedule")) is None
    assert grants.get(("api_app", "ingestion_incident")) is None

    # admin_bypass — no static grants on either of the operator-only tables.
    assert grants.get(("admin_bypass", "document_poll_schedule")) is None
    assert grants.get(("admin_bypass", "ingestion_incident")) is None


@pytest.mark.integration
def test_ingestion_worker_update_on_document_versions_is_column_scoped(
    migrated_engine: Engine,
) -> None:
    """``ingestion_worker`` has UPDATE on ``valid_to`` only."""
    try:
        with migrated_engine.connect() as conn:
            cols = {
                row.column_name
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.column_privileges
                        WHERE table_schema = 'public'
                          AND table_name = 'document_versions'
                          AND grantee = 'ingestion_worker'
                          AND privilege_type = 'UPDATE'
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert cols == {"valid_to"}


@pytest.mark.integration
def test_ingestion_incident_id_is_bigserial(
    migrated_engine: Engine,
) -> None:
    """Inserting two incidents yields monotonically increasing bigint IDs."""
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            first = conn.execute(
                text(
                    "INSERT INTO ingestion_incident "
                    "(document_id, error_class, payload) "
                    "VALUES (:d, :c, '{}'::jsonb) RETURNING id"
                ),
                {"d": doc_id, "c": "transient"},
            ).scalar_one()
            second = conn.execute(
                text(
                    "INSERT INTO ingestion_incident "
                    "(document_id, error_class, payload) "
                    "VALUES (:d, :c, '{}'::jsonb) RETURNING id"
                ),
                {"d": doc_id, "c": "persistent"},
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert isinstance(first, int)
    assert isinstance(second, int)
    assert second == first + 1


@pytest.mark.integration
def test_claim_loop_skip_locked_query_runs(
    migrated_engine: Engine,
) -> None:
    """The SKIP LOCKED claim-loop SQL from ADR-0001 parses and runs against
    the schema. Smoke-level — concurrent-claim semantics are exercised by
    WU3.3, but this asserts the schema shape supports the pattern.
    """
    try:
        with migrated_engine.begin() as conn:
            doc_id = _insert_document(conn)
            conn.execute(
                text(
                    "INSERT INTO document_poll_schedule "
                    "(document_id, cadence_interval, next_poll_at) "
                    "VALUES (:d, :c, :n)"
                ),
                {
                    "d": doc_id,
                    "c": timedelta(hours=24),
                    "n": datetime.now(UTC) - timedelta(minutes=1),
                },
            )

        with migrated_engine.begin() as conn:
            rows = list(
                conn.execute(
                    text(
                        "SELECT document_id FROM document_poll_schedule "
                        "WHERE next_poll_at <= now() AND failure_count <= 5 "
                        "ORDER BY next_poll_at "
                        "FOR UPDATE SKIP LOCKED LIMIT 8"
                    )
                )
            )
    finally:
        migrated_engine.dispose()

    assert doc_id in {row.document_id for row in rows}

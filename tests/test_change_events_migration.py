"""Integration test for the WU3.4 ``change_events`` migration (0009).

Applies the Alembic tree against a fresh Postgres 18 container and
asserts the resulting schema and behaviour:

- ``change_events`` exists with the WU3.4 real-column shape, owned by
  ``schema_owner``, with the three documented indexes.
- ``change_type`` CHECK rejects values outside
  ``{ADDED, REMOVED, MODIFIED, MOVED}``.
- ``alignment_confidence`` CHECK rejects ``<= 0`` and ``> 1.0``.
- Grants: ``api_app`` has SELECT only; ``ingestion_worker`` has SELECT
  + INSERT and USAGE on the sequence; ``admin_bypass`` has SELECT.
- Append-only trigger rejects UPDATE and DELETE.
- RLS is enabled + FORCE'd; the ``change_events_in_scope`` and
  ``change_events_ingestion_all`` policies are attached.

Sync, like the other migration tests.
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


@pytest.fixture
def migrated_engine(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> Engine:
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    monkeypatch.setenv("HORIZONS_DB_URL", sync_url)
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    return create_engine(sync_url, future=True)


def _sha256_32() -> bytes:
    return hashlib.sha256(b"hello-wu34").digest()


def _insert_document_and_version(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    doc_id = conn.execute(
        text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {
            "j": "IE",
            "s": "BANKING",
            "lid": f"upstream-{uuid.uuid4()}",
            "t": "Sample Act",
        },
    ).scalar_one()
    version_id = conn.execute(
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
            "l": "v1",
            "vn": 1,
            "vf": datetime.now(UTC),
            "vt": None,
            "p": datetime.now(UTC),
            "e": datetime.now(UTC) + timedelta(days=20),
            "c": "originals",
            "k": f"{_sha256_32().hex()}.md",
            "h": _sha256_32(),
            "b": 4096,
        },
    ).scalar_one()
    return doc_id, version_id


@pytest.mark.integration
def test_change_events_table_exists_with_expected_columns(
    migrated_engine: Engine,
) -> None:
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
                          AND table_name = 'change_events'
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert cols["id"] == ("bigint", "NO")
    assert cols["document_id"] == ("uuid", "NO")
    assert cols["document_version_id"] == ("uuid", "NO")
    assert cols["jurisdiction"] == ("text", "NO")
    assert cols["sector"] == ("text", "NO")
    assert cols["change_type"] == ("text", "NO")
    assert cols["before_clause_uid"] == ("uuid", "YES")
    assert cols["after_clause_uid"] == ("uuid", "YES")
    assert cols["before_path"] == ("text", "YES")
    assert cols["after_path"] == ("text", "YES")
    assert cols["before_text"] == ("text", "YES")
    assert cols["after_text"] == ("text", "YES")
    assert cols["alignment_confidence"] == ("double precision", "NO")
    assert cols["detected_at"] == ("timestamp with time zone", "NO")
    assert cols["effective_date"] == ("timestamp with time zone", "YES")


@pytest.mark.integration
def test_change_events_owned_by_schema_owner(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            owner = conn.execute(
                text(
                    """
                    SELECT pg_get_userbyid(c.relowner) AS owner
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'change_events'
                    """
                )
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert owner == "schema_owner"


@pytest.mark.integration
def test_expected_indexes_present(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            indexes = {
                row.indexname
                for row in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'change_events'")
                )
            }
    finally:
        migrated_engine.dispose()

    assert "idx_change_events_scope" in indexes
    assert "idx_change_events_document" in indexes
    assert "idx_change_events_version" in indexes


@pytest.mark.integration
def test_change_type_check_constraint_rejects_bad_values(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.begin() as conn:
            doc_id, version_id = _insert_document_and_version(conn)

        with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO change_events "
                    "(document_id, document_version_id, jurisdiction, sector, "
                    "change_type, alignment_confidence) "
                    "VALUES (:d, :v, 'IE', 'BANKING', 'BANANAS', 0.9)"
                ),
                {"d": doc_id, "v": version_id},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_alignment_confidence_check_rejects_out_of_range(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.begin() as conn:
            doc_id, version_id = _insert_document_and_version(conn)

        for bad in (0.0, -0.1, 1.1):
            with (
                pytest.raises(IntegrityError),
                migrated_engine.begin() as conn,
            ):
                conn.execute(
                    text(
                        "INSERT INTO change_events "
                        "(document_id, document_version_id, jurisdiction, sector, "
                        "change_type, alignment_confidence) "
                        "VALUES (:d, :v, 'IE', 'BANKING', 'ADDED', :c)"
                    ),
                    {"d": doc_id, "v": version_id, "c": bad},
                )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_append_only_trigger_rejects_update_and_delete(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.begin() as conn:
            doc_id, version_id = _insert_document_and_version(conn)
            event_id = conn.execute(
                text(
                    "INSERT INTO change_events "
                    "(document_id, document_version_id, jurisdiction, sector, "
                    "change_type, alignment_confidence) "
                    "VALUES (:d, :v, 'IE', 'BANKING', 'ADDED', 1.0) RETURNING id"
                ),
                {"d": doc_id, "v": version_id},
            ).scalar_one()

        with pytest.raises(DBAPIError), migrated_engine.begin() as conn:
            conn.execute(
                text("UPDATE change_events SET alignment_confidence = 0.5 WHERE id = :i"),
                {"i": event_id},
            )

        with pytest.raises(DBAPIError), migrated_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM change_events WHERE id = :i"),
                {"i": event_id},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_grants_match_design(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            grants = {
                (row.grantee, row.privilege_type)
                for row in conn.execute(
                    text(
                        """
                        SELECT grantee, privilege_type
                        FROM information_schema.role_table_grants
                        WHERE table_schema = 'public'
                          AND table_name = 'change_events'
                        """
                    )
                )
            }
            sequence_grants = {
                (row.grantee, row.privilege_type)
                for row in conn.execute(
                    text(
                        """
                        SELECT grantee, privilege_type
                        FROM information_schema.role_usage_grants
                        WHERE object_schema = 'public'
                          AND object_name = 'change_events_id_seq'
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert ("api_app", "SELECT") in grants
    assert ("ingestion_worker", "SELECT") in grants
    assert ("ingestion_worker", "INSERT") in grants
    assert ("admin_bypass", "SELECT") in grants
    # No write grants for api_app or admin_bypass.
    assert ("api_app", "INSERT") not in grants
    assert ("api_app", "UPDATE") not in grants
    assert ("admin_bypass", "INSERT") not in grants
    assert ("admin_bypass", "UPDATE") not in grants
    # Sequence grants — ingestion_worker needs USAGE so bigserial DEFAULT
    # advances under the role's session.
    assert ("ingestion_worker", "USAGE") in sequence_grants


@pytest.mark.integration
def test_rls_enabled_with_expected_policies(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            rls_row = conn.execute(
                text(
                    """
                    SELECT relrowsecurity, relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'change_events'
                    """
                )
            ).one()
            policies = {
                row.policyname
                for row in conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = 'public' AND tablename = 'change_events'"
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert rls_row.relrowsecurity is True
    assert rls_row.relforcerowsecurity is True
    assert "change_events_in_scope" in policies
    assert "change_events_ingestion_all" in policies


@pytest.mark.integration
def test_insert_succeeds_with_full_shape(migrated_engine: Engine) -> None:
    """Round-trip a representative MODIFIED row to prove every column accepts."""
    try:
        with migrated_engine.begin() as conn:
            doc_id, version_id = _insert_document_and_version(conn)
            before_uid = uuid.uuid4()
            after_uid = uuid.uuid4()
            event_id = conn.execute(
                text(
                    "INSERT INTO change_events "
                    "(document_id, document_version_id, jurisdiction, sector, "
                    "change_type, before_clause_uid, after_clause_uid, "
                    "before_path, after_path, before_text, after_text, "
                    "alignment_confidence, effective_date) "
                    "VALUES (:d, :v, 'IE', 'BANKING', 'MODIFIED', :bu, :au, "
                    ":bp, :ap, :bt, :at, :c, :ed) RETURNING id"
                ),
                {
                    "d": doc_id,
                    "v": version_id,
                    "bu": before_uid,
                    "au": after_uid,
                    "bp": "Part 1/Section 4",
                    "ap": "Part 1/Section 4",
                    "bt": "The Minister may by order ...",
                    "at": "The Minister shall by order ...",
                    "c": 0.94,
                    "ed": datetime.now(UTC) + timedelta(days=20),
                },
            ).scalar_one()
        with migrated_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT change_type, before_clause_uid, after_clause_uid, "
                    "before_path, after_path, before_text, after_text, "
                    "alignment_confidence, detected_at "
                    "FROM change_events WHERE id = :i"
                ),
                {"i": event_id},
            ).one()
    finally:
        migrated_engine.dispose()

    assert row.change_type == "MODIFIED"
    assert row.before_clause_uid == before_uid
    assert row.after_clause_uid == after_uid
    assert row.before_path == "Part 1/Section 4"
    assert abs(float(row.alignment_confidence) - 0.94) < 1e-9
    assert row.detected_at is not None

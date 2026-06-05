"""Integration test for the WU1.4 ``watchlists`` table migration.

Applies the Alembic tree against a fresh Postgres 18 container and
asserts the resulting schema and behaviour of the ``watchlists`` table
itself — the cross-client privacy behaviour driven by RLS lives in
``test_rls_watchlists.py``.

Coverage here:

- Columns, types, NOT NULL.
- Owner is ``schema_owner``.
- ``idx_watchlists_user_id`` index exists.
- ``api_app`` has SELECT, INSERT, UPDATE, DELETE; other roles have
  no static grants.
- RLS is enabled and FORCEd on the table.
- ``uuidv7()`` PK default is honoured.
- INSERT / UPDATE / DELETE all succeed (no append-only trigger).
- ``ON DELETE CASCADE`` from ``users`` removes the watchlist row.

Sync, like the other migration tests — Alembic is a sync API and
pytest-asyncio's function-scoped event loop conflicts with the
session-scoped async engine fixture in ``conftest.py``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

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
    """Apply Alembic head and yield a sync engine pointed at the container."""
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    monkeypatch.setenv("HORIZONS_DB_URL", sync_url)
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    return create_engine(sync_url, future=True)


def _make_user(conn: Connection, email: str) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO users (email, password_hash, role) "
            "VALUES (:e, 'hash', 'client') RETURNING id"
        ),
        {"e": email},
    ).scalar_one()


def _make_document(
    conn: Connection,
    lawstronaut_id: str,
    jurisdiction: str = "ie",
    sector: str = "legal",
    title: str = "wl_test_doc",
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO documents (jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :l, :t) RETURNING id"
        ),
        {"j": jurisdiction, "s": sector, "l": lawstronaut_id, "t": title},
    ).scalar_one()


def _make_watchlist(
    conn: Connection,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    name: str = "wl_default",
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO watchlists (user_id, document_id, name) VALUES (:u, :d, :n) RETURNING id"
        ),
        {"u": user_id, "d": document_id, "n": name},
    ).scalar_one()


@pytest.mark.integration
def test_watchlists_columns(migrated_engine: Engine) -> None:
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
                          AND table_name = 'watchlists'
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert cols["id"] == ("uuid", "NO")
    assert cols["user_id"] == ("uuid", "NO")
    assert cols["document_id"] == ("uuid", "NO")
    assert cols["name"] == ("text", "NO")
    assert cols["created_at"] == ("timestamp with time zone", "NO")


@pytest.mark.integration
def test_watchlists_owned_by_schema_owner(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            owner = conn.execute(
                text(
                    """
                    SELECT pg_get_userbyid(c.relowner) AS owner
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'watchlists'
                    """
                )
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert owner == "schema_owner"


@pytest.mark.integration
def test_watchlists_index_present(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            indexes = {
                row.indexname
                for row in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'watchlists'")
                )
            }
    finally:
        migrated_engine.dispose()

    assert "idx_watchlists_user_id" in indexes


@pytest.mark.integration
def test_watchlists_grants(migrated_engine: Engine) -> None:
    """api_app gets full CRUD; ingestion_worker has no grant; admin_bypass
    holds SELECT + UPDATE (the WU4.5 reduction path soft-hides rows by
    flipping ``active=false`` under admin_bypass)."""
    try:
        with migrated_engine.connect() as conn:
            rows = list(
                conn.execute(
                    text(
                        """
                        SELECT grantee, privilege_type
                        FROM information_schema.role_table_grants
                        WHERE table_schema = 'public'
                          AND table_name = 'watchlists'
                          AND grantee IN (
                              'api_app', 'ingestion_worker', 'admin_bypass'
                          )
                        """
                    )
                )
            )
    finally:
        migrated_engine.dispose()

    grants: dict[str, set[str]] = {}
    for row in rows:
        grants.setdefault(row.grantee, set()).add(row.privilege_type)

    assert grants.get("api_app") == {"SELECT", "INSERT", "UPDATE", "DELETE"}
    assert "ingestion_worker" not in grants
    assert grants.get("admin_bypass") == {"SELECT", "UPDATE"}


@pytest.mark.integration
def test_watchlists_rls_enabled_and_forced(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'watchlists'
                    """
                )
            ).one()
    finally:
        migrated_engine.dispose()

    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


@pytest.mark.integration
def test_watchlists_uuidv7_default(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "wl_v7@example.com")
            did = _make_document(conn, "wl_v7_doc")
            wid = _make_watchlist(conn, uid, did, "wl_v7")
    finally:
        migrated_engine.dispose()

    assert isinstance(wid, uuid.UUID)
    assert wid.version == 7


@pytest.mark.integration
def test_watchlists_insert_update_delete_all_permitted(
    migrated_engine: Engine,
) -> None:
    """No append-only trigger — rename and delete are real operations."""
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "wl_crud@example.com")
            did = _make_document(conn, "wl_crud_doc")
            wid = _make_watchlist(conn, uid, did, "wl_crud_original")

        with migrated_engine.begin() as conn:
            conn.execute(
                text("UPDATE watchlists SET name = :n WHERE id = :id"),
                {"n": "wl_crud_renamed", "id": wid},
            )
        with migrated_engine.connect() as conn:
            name = conn.execute(
                text("SELECT name FROM watchlists WHERE id = :id"),
                {"id": wid},
            ).scalar_one()
            assert name == "wl_crud_renamed"

        with migrated_engine.begin() as conn:
            deleted = conn.execute(
                text("DELETE FROM watchlists WHERE id = :id RETURNING id"),
                {"id": wid},
            ).scalar_one()
            assert deleted == wid
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_watchlists_cascade_on_user_delete(migrated_engine: Engine) -> None:
    """Deleting the parent user removes the watchlist via ON DELETE CASCADE."""
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "wl_cascade@example.com")
            did = _make_document(conn, "wl_cascade_doc")
            wid = _make_watchlist(conn, uid, did, "wl_cascade")

        with migrated_engine.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})

        with migrated_engine.connect() as conn:
            remaining = conn.execute(
                text("SELECT 1 FROM watchlists WHERE id = :id"),
                {"id": wid},
            ).first()
            assert remaining is None
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_watchlists_user_document_unique_constraint(
    migrated_engine: Engine,
) -> None:
    """A user cannot watch the same document twice."""
    from sqlalchemy.exc import IntegrityError

    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "wl_unique@example.com")
            did = _make_document(conn, "wl_unique_doc")
            _make_watchlist(conn, uid, did, "wl_unique_first")

        with (
            pytest.raises(IntegrityError),
            migrated_engine.begin() as conn,
        ):
            _make_watchlist(conn, uid, did, "wl_unique_dup")
    finally:
        migrated_engine.dispose()

"""Integration test for the WU4.0 ``refresh_tokens`` table migration.

Applies the Alembic tree against a fresh Postgres 18 container and
asserts the schema + role-grant + RLS shape. Refresh-flow behaviour
(record / revoke through the auth provider) lives in
``test_refresh_tokens_repo.py``.

Coverage here:

- Columns, types, NOT NULL.
- Owner is ``schema_owner``.
- ``idx_refresh_tokens_user_id_issued_at`` index exists.
- ``api_app`` has SELECT, INSERT, UPDATE (no DELETE);
  ``admin_bypass`` has SELECT; ``ingestion_worker`` has nothing.
- RLS enabled and FORCEd; the three policies (owner-scoped SELECT /
  INSERT / UPDATE) exist for ``api_app``.
- ``ON DELETE CASCADE`` from ``users`` removes the refresh-token row.

Sync, matching the other migration tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
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


@pytest.mark.integration
def test_refresh_tokens_columns(migrated_engine: Engine) -> None:
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
                          AND table_name = 'refresh_tokens'
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert cols["jti"] == ("uuid", "NO")
    assert cols["user_id"] == ("uuid", "NO")
    assert cols["issued_at"] == ("timestamp with time zone", "NO")
    assert cols["expires_at"] == ("timestamp with time zone", "NO")
    assert cols["revoked_at"] == ("timestamp with time zone", "YES")


@pytest.mark.integration
def test_refresh_tokens_owned_by_schema_owner(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            owner = conn.execute(
                text(
                    """
                    SELECT pg_get_userbyid(c.relowner) AS owner
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'refresh_tokens'
                    """
                )
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert owner == "schema_owner"


@pytest.mark.integration
def test_refresh_tokens_index_present(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.connect() as conn:
            indexes = {
                row.indexname
                for row in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'refresh_tokens'")
                )
            }
    finally:
        migrated_engine.dispose()

    assert "idx_refresh_tokens_user_id_issued_at" in indexes


@pytest.mark.integration
def test_refresh_tokens_grants(migrated_engine: Engine) -> None:
    """api_app: SELECT/INSERT/UPDATE; admin_bypass: SELECT; worker: none."""
    try:
        with migrated_engine.connect() as conn:
            rows = list(
                conn.execute(
                    text(
                        """
                        SELECT grantee, privilege_type
                        FROM information_schema.role_table_grants
                        WHERE table_schema = 'public'
                          AND table_name = 'refresh_tokens'
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

    assert grants.get("api_app") == {"SELECT", "INSERT", "UPDATE"}
    assert grants.get("admin_bypass") == {"SELECT"}
    assert "ingestion_worker" not in grants


@pytest.mark.integration
def test_refresh_tokens_rls_forced_and_policies_present(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.connect() as conn:
            rls_state = conn.execute(
                text(
                    """
                    SELECT relrowsecurity, relforcerowsecurity
                    FROM pg_class
                    WHERE relname = 'refresh_tokens'
                    """
                )
            ).one()
            policies = {
                row.policyname
                for row in conn.execute(
                    text("SELECT policyname FROM pg_policies WHERE tablename = 'refresh_tokens'")
                )
            }
    finally:
        migrated_engine.dispose()

    assert rls_state.relrowsecurity is True
    assert rls_state.relforcerowsecurity is True
    assert {
        "refresh_tokens_owner_select",
        "refresh_tokens_owner_insert",
        "refresh_tokens_owner_update",
    } <= policies


@pytest.mark.integration
def test_refresh_tokens_cascade_on_user_delete(
    migrated_engine: Engine,
) -> None:
    """Deleting the owning user cascades to their refresh-token rows."""
    suffix = uuid.uuid4().hex[:8]
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, f"rt_cascade_{suffix}@example.test")
            jti = uuid.uuid4()
            now = datetime.now(UTC)
            conn.execute(
                text(
                    "INSERT INTO refresh_tokens (jti, user_id, issued_at, expires_at) "
                    "VALUES (:j, :u, :i, :e)"
                ),
                {
                    "j": jti,
                    "u": uid,
                    "i": now,
                    "e": now + timedelta(days=30),
                },
            )

        with migrated_engine.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})

        with migrated_engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM refresh_tokens WHERE jti = :j"),
                {"j": jti},
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert count == 0

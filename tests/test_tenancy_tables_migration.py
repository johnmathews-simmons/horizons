"""Integration test for the WU1.1 tenancy tables migration.

Applies the Alembic tree against a fresh Postgres 18 container and
asserts the resulting schema and behaviour:

- Tables, columns, types, NOT NULL, defaults, FKs.
- Ownership is ``schema_owner``.
- ENUM ``user_role`` exists with values ``{client, admin}``.
- Append-only trigger:
  * subscriptions: valid_to NULL -> ts allowed; other column UPDATE rejected.
  * subscription_scopes: any UPDATE rejected.
  * users: UPDATE allowed (password/email rotation).
- CHECK on ``subscriptions.valid_to > valid_from``.
- ``uuidv7()`` default returns a UUID on INSERT.

Sync, like the role-model test — Alembic is a sync API and clashes with
the session-scoped async engine fixture's event loop. A short-lived
sync SQLAlchemy engine for the assertion queries sidesteps that.
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
from sqlalchemy.exc import DBAPIError, IntegrityError

if TYPE_CHECKING:
    from sqlalchemy import Engine
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


@pytest.mark.integration
def test_tenancy_tables_exist_with_expected_columns(
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
                              'users', 'subscriptions', 'subscription_scopes'
                          )
                        """
                    )
                )
            }
    finally:
        migrated_engine.dispose()

    assert cols[("users", "id")] == ("uuid", "NO")
    assert cols[("users", "email")] == ("text", "NO")
    assert cols[("users", "password_hash")] == ("text", "NO")
    assert cols[("users", "role")] == ("USER-DEFINED", "NO")
    assert cols[("users", "created_at")] == ("timestamp with time zone", "NO")

    assert cols[("subscriptions", "id")] == ("uuid", "NO")
    assert cols[("subscriptions", "user_id")] == ("uuid", "NO")
    assert cols[("subscriptions", "valid_from")] == (
        "timestamp with time zone",
        "NO",
    )
    assert cols[("subscriptions", "valid_to")] == (
        "timestamp with time zone",
        "YES",
    )
    assert cols[("subscriptions", "created_at")] == (
        "timestamp with time zone",
        "NO",
    )

    assert cols[("subscription_scopes", "subscription_id")] == ("uuid", "NO")
    assert cols[("subscription_scopes", "jurisdiction")] == ("text", "NO")
    assert cols[("subscription_scopes", "sector")] == ("text", "NO")


@pytest.mark.integration
def test_schema_objects_owned_by_schema_owner(
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
                              'users', 'subscriptions', 'subscription_scopes'
                          )
                        """
                    )
                )
            }
            enum_owner = conn.execute(
                text(
                    "SELECT pg_get_userbyid(typowner) AS owner "
                    "FROM pg_type WHERE typname = 'user_role'"
                )
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert owners == {
        "users": "schema_owner",
        "subscriptions": "schema_owner",
        "subscription_scopes": "schema_owner",
    }
    assert enum_owner == "schema_owner"


@pytest.mark.integration
def test_user_role_enum_has_client_and_admin(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.connect() as conn:
            values = sorted(
                row.enumlabel
                for row in conn.execute(
                    text(
                        """
                        SELECT e.enumlabel
                        FROM pg_type t
                        JOIN pg_enum e ON e.enumtypid = t.oid
                        WHERE t.typname = 'user_role'
                        """
                    )
                )
            )
    finally:
        migrated_engine.dispose()

    assert values == ["admin", "client"]


@pytest.mark.integration
def test_users_insert_returns_uuidv7_default(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.begin() as conn:
            returned = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES ('a@example.com', 'hash', 'client') "
                    "RETURNING id"
                )
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert isinstance(returned, uuid.UUID)
    # UUIDv7 lives in version field 0x7.
    assert returned.version == 7


@pytest.mark.integration
def test_users_update_is_allowed(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.begin() as conn:
            uid = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES ('rotate@example.com', 'old', 'client') "
                    "RETURNING id"
                )
            ).scalar_one()
            conn.execute(
                text("UPDATE users SET password_hash = 'new' WHERE id = :id"),
                {"id": uid},
            )
            after = conn.execute(
                text("SELECT password_hash FROM users WHERE id = :id"),
                {"id": uid},
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert after == "new"


@pytest.mark.integration
def test_subscriptions_check_rejects_inverted_validity(
    migrated_engine: Engine,
) -> None:
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            uid = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES ('check@example.com', 'hash', 'client') "
                    "RETURNING id"
                )
            ).scalar_one()
        with (
            pytest.raises(IntegrityError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(user_id, valid_from, valid_to) "
                    "VALUES (:u, :start, :end)"
                ),
                {"u": uid, "start": now, "end": now - timedelta(days=1)},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_subscriptions_allow_ending_valid_to(
    migrated_engine: Engine,
) -> None:
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            uid = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES ('end@example.com', 'hash', 'client') "
                    "RETURNING id"
                )
            ).scalar_one()
            sid = conn.execute(
                text(
                    "INSERT INTO subscriptions (user_id, valid_from) "
                    "VALUES (:u, :start) RETURNING id"
                ),
                {"u": uid, "start": now - timedelta(days=30)},
            ).scalar_one()
            conn.execute(
                text("UPDATE subscriptions SET valid_to = :end WHERE id = :id"),
                {"end": now, "id": sid},
            )
            after = conn.execute(
                text("SELECT valid_to FROM subscriptions WHERE id = :id"),
                {"id": sid},
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert after is not None


@pytest.mark.integration
def test_subscriptions_reject_other_updates(migrated_engine: Engine) -> None:
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            uid = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES ('reject@example.com', 'hash', 'client') "
                    "RETURNING id"
                )
            ).scalar_one()
            sid = conn.execute(
                text(
                    "INSERT INTO subscriptions (user_id, valid_from) "
                    "VALUES (:u, :start) RETURNING id"
                ),
                {"u": uid, "start": now - timedelta(days=30)},
            ).scalar_one()
        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text("UPDATE subscriptions SET valid_from = :start WHERE id = :id"),
                {"start": now, "id": sid},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_subscriptions_reject_resetting_valid_to_to_null(
    migrated_engine: Engine,
) -> None:
    """Once a subscription has ended, it cannot be revived in place.

    The valid transition is NULL -> non-NULL. The reverse (revival) must
    be a new ``subscriptions`` row, not an UPDATE.
    """
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            uid = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES ('revive@example.com', 'hash', 'client') "
                    "RETURNING id"
                )
            ).scalar_one()
            sid = conn.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(user_id, valid_from, valid_to) "
                    "VALUES (:u, :start, :end) RETURNING id"
                ),
                {
                    "u": uid,
                    "start": now - timedelta(days=30),
                    "end": now - timedelta(days=1),
                },
            ).scalar_one()
        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text("UPDATE subscriptions SET valid_to = NULL WHERE id = :id"),
                {"id": sid},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_subscription_scopes_reject_any_update(
    migrated_engine: Engine,
) -> None:
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            uid = conn.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) "
                    "VALUES ('scope@example.com', 'hash', 'client') "
                    "RETURNING id"
                )
            ).scalar_one()
            sid = conn.execute(
                text(
                    "INSERT INTO subscriptions (user_id, valid_from) "
                    "VALUES (:u, :start) RETURNING id"
                ),
                {"u": uid, "start": now - timedelta(days=30)},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO subscription_scopes "
                    "(subscription_id, jurisdiction, sector) "
                    "VALUES (:s, 'UK', 'BANKING')"
                ),
                {"s": sid},
            )
        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(
                text(
                    "UPDATE subscription_scopes SET sector = 'INSURANCE' WHERE subscription_id = :s"
                ),
                {"s": sid},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_index_on_subscriptions_user_id_valid_from_exists(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.connect() as conn:
            indexes = {
                row.indexname
                for row in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'subscriptions'")
                )
            }
    finally:
        migrated_engine.dispose()

    assert "idx_subscriptions_user_id_valid_from" in indexes

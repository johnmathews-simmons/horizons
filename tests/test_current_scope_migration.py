"""Integration test for the WU1.3 ``app_private.current_scope()`` function.

Applies the Alembic tree against a fresh Postgres 18 container and asserts:

- ``app_private`` schema exists, owned by ``schema_owner``.
- Function ``app_private.current_scope()`` exists with the documented
  signature, language (``plpgsql``), volatility (``STABLE``), security
  (``SECURITY DEFINER``), and search_path (``''``).
- Function is owned by ``schema_owner``.
- ``api_app`` has EXECUTE on the function; ``PUBLIC`` does not.
- Behavioural: active subscription returns its scopes; expired returns
  zero rows; overlapping subscriptions return the DISTINCT union of
  scopes; unset ``app.user_id`` raises.

Sync, like the role-model and tenancy tests — Alembic is a sync API and
pytest-asyncio's function-scoped event loop conflicts with the
session-scoped async engine fixture in ``conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.engine import Connection
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


# --- Schema / metadata ---------------------------------------------------


@pytest.mark.integration
def test_app_private_schema_exists_and_owned_by_schema_owner(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.connect() as conn:
            owner = conn.execute(
                text(
                    """
                    SELECT pg_get_userbyid(nspowner) AS owner
                    FROM pg_namespace
                    WHERE nspname = 'app_private'
                    """
                )
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert owner == "schema_owner"


@pytest.mark.integration
def test_current_scope_function_has_expected_signature(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        p.prolang::regtype::text AS lang_oid_type,
                        l.lanname              AS lang,
                        p.provolatile          AS volatility,
                        p.prosecdef            AS security_definer,
                        pg_get_userbyid(p.proowner) AS owner,
                        pg_get_function_result(p.oid) AS result_type,
                        pg_get_function_arguments(p.oid) AS args,
                        p.proconfig            AS config
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    JOIN pg_language l ON l.oid = p.prolang
                    WHERE n.nspname = 'app_private'
                      AND p.proname = 'current_scope'
                    """
                )
            ).one()
    finally:
        migrated_engine.dispose()

    assert row.lang == "plpgsql"
    # 's' = STABLE, 'i' = IMMUTABLE, 'v' = VOLATILE
    assert row.volatility == "s"
    assert row.security_definer is True
    assert row.owner == "schema_owner"
    assert row.result_type == "TABLE(jurisdiction text, sector text)"
    assert row.args == ""
    # search_path is set to empty string at function-config level.
    assert row.config is not None
    assert "search_path=" in row.config[0]
    # accept either `search_path=` or `search_path=""`
    assert row.config[0].rstrip('"').endswith("search_path=")


@pytest.mark.integration
def test_current_scope_execute_granted_to_api_app_only(
    migrated_engine: Engine,
) -> None:
    try:
        with migrated_engine.connect() as conn:
            # has_function_privilege returns NULL for nonexistent roles;
            # we want a boolean for each named role.
            api_app_can_execute = conn.execute(
                text(
                    "SELECT has_function_privilege("
                    "'api_app', 'app_private.current_scope()', 'EXECUTE')"
                )
            ).scalar_one()
            ingestion_can_execute = conn.execute(
                text(
                    "SELECT has_function_privilege("
                    "'ingestion_worker', 'app_private.current_scope()', 'EXECUTE')"
                )
            ).scalar_one()
            admin_bypass_can_execute = conn.execute(
                text(
                    "SELECT has_function_privilege("
                    "'admin_bypass', 'app_private.current_scope()', 'EXECUTE')"
                )
            ).scalar_one()
            public_can_execute = conn.execute(
                text(
                    "SELECT has_function_privilege("
                    "'public', 'app_private.current_scope()', 'EXECUTE')"
                )
            ).scalar_one()
    finally:
        migrated_engine.dispose()

    assert api_app_can_execute is True
    assert ingestion_can_execute is False
    assert admin_bypass_can_execute is False
    assert public_can_execute is False


# --- Behaviour -----------------------------------------------------------


def _make_user(conn: Connection, email: str) -> object:
    return conn.execute(
        text(
            "INSERT INTO users (email, password_hash, role) "
            "VALUES (:e, 'hash', 'client') RETURNING id"
        ),
        {"e": email},
    ).scalar_one()


def _make_subscription(
    conn: Connection,
    user_id: object,
    valid_from: datetime,
    valid_to: datetime | None,
    scopes: list[tuple[str, str]],
) -> object:
    sid = conn.execute(
        text(
            "INSERT INTO subscriptions (user_id, valid_from, valid_to) "
            "VALUES (:u, :f, :t) RETURNING id"
        ),
        {"u": user_id, "f": valid_from, "t": valid_to},
    ).scalar_one()
    for jurisdiction, sector in scopes:
        conn.execute(
            text(
                "INSERT INTO subscription_scopes "
                "(subscription_id, jurisdiction, sector) "
                "VALUES (:s, :j, :sec)"
            ),
            {"s": sid, "j": jurisdiction, "sec": sector},
        )
    return sid


@pytest.mark.integration
def test_current_scope_returns_active_subscription_scopes(
    migrated_engine: Engine,
) -> None:
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "active@example.com")
            _make_subscription(
                conn,
                uid,
                valid_from=now - timedelta(days=30),
                valid_to=None,
                scopes=[("UK", "BANKING"), ("UK", "INSURANCE")],
            )
        with migrated_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(uid)})
            rows = sorted(
                (r.jurisdiction, r.sector)
                for r in conn.execute(text("SELECT * FROM app_private.current_scope()"))
            )
    finally:
        migrated_engine.dispose()

    assert rows == [("UK", "BANKING"), ("UK", "INSURANCE")]


@pytest.mark.integration
def test_current_scope_excludes_expired_subscriptions(
    migrated_engine: Engine,
) -> None:
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "expired@example.com")
            _make_subscription(
                conn,
                uid,
                valid_from=now - timedelta(days=60),
                valid_to=now - timedelta(days=1),
                scopes=[("UK", "BANKING")],
            )
        with migrated_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(uid)})
            rows = list(conn.execute(text("SELECT * FROM app_private.current_scope()")))
    finally:
        migrated_engine.dispose()

    assert rows == []


@pytest.mark.integration
def test_current_scope_excludes_future_subscriptions(
    migrated_engine: Engine,
) -> None:
    """A subscription whose ``valid_from`` is in the future is not yet active."""
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "future@example.com")
            _make_subscription(
                conn,
                uid,
                valid_from=now + timedelta(days=7),
                valid_to=None,
                scopes=[("EU", "BANKING")],
            )
        with migrated_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(uid)})
            rows = list(conn.execute(text("SELECT * FROM app_private.current_scope()")))
    finally:
        migrated_engine.dispose()

    assert rows == []


@pytest.mark.integration
def test_current_scope_returns_distinct_union_of_overlapping_subscriptions(
    migrated_engine: Engine,
) -> None:
    """Two active subs sharing (UK, BANKING) collapse to one row."""
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "overlap@example.com")
            _make_subscription(
                conn,
                uid,
                valid_from=now - timedelta(days=30),
                valid_to=None,
                scopes=[("UK", "BANKING"), ("UK", "INSURANCE")],
            )
            _make_subscription(
                conn,
                uid,
                valid_from=now - timedelta(days=10),
                valid_to=None,
                scopes=[("UK", "BANKING"), ("EU", "BANKING")],
            )
        with migrated_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(uid)})
            rows = sorted(
                (r.jurisdiction, r.sector)
                for r in conn.execute(text("SELECT * FROM app_private.current_scope()"))
            )
    finally:
        migrated_engine.dispose()

    assert rows == [
        ("EU", "BANKING"),
        ("UK", "BANKING"),
        ("UK", "INSURANCE"),
    ]


@pytest.mark.integration
def test_current_scope_raises_when_app_user_id_unset(
    migrated_engine: Engine,
) -> None:
    try:
        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(text("SELECT * FROM app_private.current_scope()"))
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_current_scope_isolates_users(migrated_engine: Engine) -> None:
    """A's GUC must not see B's scopes."""
    now = datetime.now(UTC)
    try:
        with migrated_engine.begin() as conn:
            a = _make_user(conn, "iso_a@example.com")
            b = _make_user(conn, "iso_b@example.com")
            _make_subscription(
                conn,
                a,
                valid_from=now - timedelta(days=30),
                valid_to=None,
                scopes=[("UK", "BANKING")],
            )
            _make_subscription(
                conn,
                b,
                valid_from=now - timedelta(days=30),
                valid_to=None,
                scopes=[("EU", "INSURANCE")],
            )
        with migrated_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(a)})
            a_rows = sorted(
                (r.jurisdiction, r.sector)
                for r in conn.execute(text("SELECT * FROM app_private.current_scope()"))
            )
        with migrated_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": str(b)})
            b_rows = sorted(
                (r.jurisdiction, r.sector)
                for r in conn.execute(text("SELECT * FROM app_private.current_scope()"))
            )
    finally:
        migrated_engine.dispose()

    assert a_rows == [("UK", "BANKING")]
    assert b_rows == [("EU", "INSURANCE")]

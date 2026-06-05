"""Integration test for the WU1.4 ``watchlists`` RLS policies.

The cross-client privacy axis. Tests run the assertions while
bracketing each transaction with ``SET LOCAL ROLE api_app`` so RLS
actually applies (the testcontainer's superuser bypasses RLS even
under ``FORCE``).

Coverage:

- Owner-only SELECT: A's GUC sees A's rows; B's GUC sees only B's
  rows; missing GUC (NULL ``app.user_id``) sees nothing.
- INSERT ``WITH CHECK``: api_app under B's GUC cannot insert a row
  carrying A's ``user_id`` (raises).
- UPDATE ``USING + WITH CHECK``: api_app under B's GUC trying to
  rename A's row is a no-op (0 rows touched, no error). Re-keying
  one's own row to point at another user via UPDATE is rejected by
  WITH CHECK.
- DELETE ``USING``: api_app under B's GUC trying to delete A's row
  is a no-op (0 rows touched, no error).
- ``admin_bypass`` (BYPASSRLS) sees every row regardless of GUC.

Sync — see other migration tests for the rationale.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    import uuid

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


def _set_app_user(conn: Connection, user_id: uuid.UUID) -> None:
    # set_config with is_local=true is the parameter-binding-safe
    # equivalent of `SET LOCAL app.user_id = '...'` — `SET LOCAL` parses
    # above the parameter binder and rejects $1 placeholders.
    conn.execute(
        text("SELECT set_config('app.user_id', :u, true)"),
        {"u": str(user_id)},
    )


def _assume_api_app(conn: Connection) -> None:
    # Role name is an identifier, not a parameter; safe to interpolate
    # because we control it. SET LOCAL auto-reverts at txn end.
    conn.execute(text("SET LOCAL ROLE api_app"))


@pytest.mark.integration
def test_owner_sees_own_watchlists_only(migrated_engine: Engine) -> None:
    """A's GUC sees A's row; B's GUC sees B's row; neither sees the other."""
    try:
        with migrated_engine.begin() as conn:
            a = _make_user(conn, "wl_rls_a@example.com")
            b = _make_user(conn, "wl_rls_b@example.com")
            conn.execute(
                text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"),
                {"u": a, "n": "wl_rls_a_only"},
            )
            conn.execute(
                text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"),
                {"u": b, "n": "wl_rls_b_only"},
            )

        with migrated_engine.begin() as conn:
            _assume_api_app(conn)
            _set_app_user(conn, a)
            a_names = sorted(r.name for r in conn.execute(text("SELECT name FROM watchlists")))

        with migrated_engine.begin() as conn:
            _assume_api_app(conn)
            _set_app_user(conn, b)
            b_names = sorted(r.name for r in conn.execute(text("SELECT name FROM watchlists")))
    finally:
        migrated_engine.dispose()

    assert a_names == ["wl_rls_a_only"]
    assert b_names == ["wl_rls_b_only"]


@pytest.mark.integration
def test_missing_app_user_id_raises_under_api_app(
    migrated_engine: Engine,
) -> None:
    """Policy uses ``current_setting('app.user_id')::uuid`` (unsafe form);
    forgetting ``SET LOCAL`` raises rather than silently returning
    nothing. Mirrors the loud-failure contract of ``current_scope()``."""
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "wl_rls_no_guc@example.com")
            conn.execute(
                text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"),
                {"u": uid, "n": "wl_rls_no_guc"},
            )

        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            _assume_api_app(conn)
            # Deliberately omit set_config — current_setting() in the
            # policy raises on unset GUC.
            conn.execute(text("SELECT name FROM watchlists")).all()
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_insert_with_check_rejects_foreign_user_id(
    migrated_engine: Engine,
) -> None:
    """api_app under B's GUC cannot insert a row owned by A."""
    try:
        with migrated_engine.begin() as conn:
            a = _make_user(conn, "wl_rls_check_a@example.com")
            b = _make_user(conn, "wl_rls_check_b@example.com")

        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            _assume_api_app(conn)
            _set_app_user(conn, b)
            conn.execute(
                text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"),
                {"u": a, "n": "wl_rls_foreign_insert"},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_update_on_others_row_is_noop(migrated_engine: Engine) -> None:
    """B cannot rename A's row — UPDATE touches 0 rows, no error raised."""
    try:
        with migrated_engine.begin() as conn:
            a = _make_user(conn, "wl_rls_upd_a@example.com")
            b = _make_user(conn, "wl_rls_upd_b@example.com")
            a_wid = conn.execute(
                text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n) RETURNING id"),
                {"u": a, "n": "wl_rls_upd_original"},
            ).scalar_one()

        with migrated_engine.begin() as conn:
            _assume_api_app(conn)
            _set_app_user(conn, b)
            result = conn.execute(
                text("UPDATE watchlists SET name = :n WHERE id = :id"),
                {"n": "wl_rls_upd_hijacked", "id": a_wid},
            )
            assert result.rowcount == 0

        # Confirm the row is unchanged when checked under A's GUC.
        with migrated_engine.begin() as conn:
            _assume_api_app(conn)
            _set_app_user(conn, a)
            name = conn.execute(
                text("SELECT name FROM watchlists WHERE id = :id"),
                {"id": a_wid},
            ).scalar_one()
            assert name == "wl_rls_upd_original"
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_update_rekeying_to_other_user_rejected(
    migrated_engine: Engine,
) -> None:
    """UPDATE that tries to move one's own row to another user's user_id
    is rejected by WITH CHECK."""
    try:
        with migrated_engine.begin() as conn:
            a = _make_user(conn, "wl_rls_rekey_a@example.com")
            b = _make_user(conn, "wl_rls_rekey_b@example.com")
            a_wid = conn.execute(
                text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n) RETURNING id"),
                {"u": a, "n": "wl_rls_rekey"},
            ).scalar_one()

        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            _assume_api_app(conn)
            _set_app_user(conn, a)
            conn.execute(
                text("UPDATE watchlists SET user_id = :new WHERE id = :id"),
                {"new": b, "id": a_wid},
            )
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_delete_on_others_row_is_noop(migrated_engine: Engine) -> None:
    """B cannot delete A's row — DELETE touches 0 rows, no error raised."""
    try:
        with migrated_engine.begin() as conn:
            a = _make_user(conn, "wl_rls_del_a@example.com")
            b = _make_user(conn, "wl_rls_del_b@example.com")
            a_wid = conn.execute(
                text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n) RETURNING id"),
                {"u": a, "n": "wl_rls_del_target"},
            ).scalar_one()

        with migrated_engine.begin() as conn:
            _assume_api_app(conn)
            _set_app_user(conn, b)
            result = conn.execute(
                text("DELETE FROM watchlists WHERE id = :id"),
                {"id": a_wid},
            )
            assert result.rowcount == 0

        # Confirm A's row survives.
        with migrated_engine.connect() as conn:
            still_there = conn.execute(
                text("SELECT 1 FROM watchlists WHERE id = :id"),
                {"id": a_wid},
            ).scalar_one()
            assert still_there == 1
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_admin_bypass_sees_all_watchlists(migrated_engine: Engine) -> None:
    """SET LOCAL ROLE admin_bypass — BYPASSRLS sees every row."""
    try:
        with migrated_engine.begin() as conn:
            a = _make_user(conn, "wl_rls_admin_a@example.com")
            b = _make_user(conn, "wl_rls_admin_b@example.com")
            conn.execute(
                text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"),
                {"u": a, "n": "wl_rls_admin_a_row"},
            )
            conn.execute(
                text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"),
                {"u": b, "n": "wl_rls_admin_b_row"},
            )

        with migrated_engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE admin_bypass"))
            # No GUC set; admin_bypass shouldn't need it.
            names = sorted(
                r.name
                for r in conn.execute(
                    text("SELECT name FROM watchlists WHERE name LIKE 'wl_rls_admin_%'")
                )
            )
    finally:
        migrated_engine.dispose()

    assert names == ["wl_rls_admin_a_row", "wl_rls_admin_b_row"]

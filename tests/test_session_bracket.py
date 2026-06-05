"""Behavioural tests for the WU1.5 session bracket.

Coverage:

- ``session_for_user(engine, user_id)`` issues
  ``set_config('app.user_id', ...)``; ``current_setting('app.user_id', true)``
  matches inside the bracket.
- Normal exit commits; writes are visible to outside readers.
- Exception exit rolls back; writes are not visible.
- ``DISCARD ALL`` on pool checkin clears session-level GUCs across
  connection reuse.
- End-to-end: the bracket plus ``SET LOCAL ROLE api_app`` plus the
  ``watchlists`` RLS policy is owner-scoped (the cross-client privacy
  axis works through the new entry point).
- ``get_session(user_id)`` (lazy-global wrapper) is the same shape
  end-to-end.

The migrated DB and the async engine are function-scoped so each test
gets a clean schema; data prefix is ``sess_`` to avoid collision with
WU1.4's ``wl_`` / ``corpus_rls_`` rows.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy
from alembic import command
from alembic.config import Config
from horizons_core.db import session as session_mod
from horizons_core.db.session import get_session, make_engine, session_for_user
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


@pytest.fixture
def migrated_db(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, str]]:
    """Run alembic migrations and yield ``(sync_engine, async_url)``."""
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    async_url = postgres_container.get_connection_url(driver="asyncpg")
    monkeypatch.setenv("HORIZONS_DB_URL", sync_url)
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    sync_engine = create_engine(sync_url, future=True)
    try:
        yield sync_engine, async_url
    finally:
        sync_engine.dispose()


@pytest_asyncio.fixture
async def async_engine(
    migrated_db: tuple[Engine, str],
) -> AsyncIterator[AsyncEngine]:
    _, async_url = migrated_db
    eng = make_engine(async_url)
    try:
        yield eng
    finally:
        await eng.dispose()


def _make_user(sync_engine: Engine, email: str) -> uuid.UUID:
    with sync_engine.begin() as conn:
        return conn.execute(
            sqlalchemy.text(
                "INSERT INTO users (email, password_hash, role) "
                "VALUES (:e, 'hash', 'client') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()


@pytest.mark.integration
async def test_bracket_sets_app_user_id(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    u = _make_user(sync, "sess_set_guc@example.com")

    async with session_for_user(async_engine, u) as session:
        got = (
            await session.execute(
                sqlalchemy.text("SELECT current_setting('app.user_id', true)")
            )
        ).scalar_one()
    assert got == str(u)


@pytest.mark.integration
async def test_bracket_commits_on_normal_exit(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    u = _make_user(sync, "sess_commit@example.com")

    async with session_for_user(async_engine, u) as session:
        await session.execute(
            sqlalchemy.text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"),
            {"u": u, "n": "sess_commit_row"},
        )

    with sync.begin() as conn:
        names = sorted(
            r.name
            for r in conn.execute(
                sqlalchemy.text("SELECT name FROM watchlists WHERE user_id = :u"),
                {"u": u},
            )
        )
    assert names == ["sess_commit_row"]


@pytest.mark.integration
async def test_bracket_rolls_back_on_exception(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    u = _make_user(sync, "sess_rollback@example.com")

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        async with session_for_user(async_engine, u) as session:
            await session.execute(
                sqlalchemy.text(
                    "INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"
                ),
                {"u": u, "n": "sess_rollback_row"},
            )
            raise _Boom

    with sync.begin() as conn:
        rows = conn.execute(
            sqlalchemy.text("SELECT name FROM watchlists WHERE user_id = :u"),
            {"u": u},
        ).all()
    assert rows == []


@pytest.mark.integration
async def test_discard_all_clears_session_gucs_on_checkin(
    migrated_db: tuple[Engine, str],
) -> None:
    """Set a SESSION-level GUC on one checkout, then reacquire from the
    pool and confirm the value did not bleed across. ``SET`` without
    ``LOCAL`` persists past commit at session scope, so anything other
    than ``'leaked'`` on the second checkout proves ``DISCARD ALL`` ran.
    """
    _, async_url = migrated_db
    eng = make_engine(async_url)
    try:
        async with eng.connect() as conn:
            await conn.execute(sqlalchemy.text("SET app.test_marker = 'leaked'"))
            await conn.commit()

        async with eng.connect() as conn:
            got = (
                await conn.execute(
                    sqlalchemy.text("SELECT current_setting('app.test_marker', true)")
                )
            ).scalar_one()
        assert got != "leaked"
    finally:
        await eng.dispose()


@pytest.mark.integration
async def test_rls_protected_read_is_user_scoped_through_bracket(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    """End-to-end: the bracket sets ``app.user_id``, the watchlists RLS
    policy keys off it, and ``api_app`` sees only the owner's row."""
    sync, _ = migrated_db
    a = _make_user(sync, "sess_e2e_a@example.com")
    b = _make_user(sync, "sess_e2e_b@example.com")
    with sync.begin() as conn:
        conn.execute(
            sqlalchemy.text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"),
            {"u": a, "n": "sess_e2e_a_row"},
        )
        conn.execute(
            sqlalchemy.text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n)"),
            {"u": b, "n": "sess_e2e_b_row"},
        )

    async with session_for_user(async_engine, a) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        a_names = sorted(
            r.name
            for r in await session.execute(
                sqlalchemy.text("SELECT name FROM watchlists")
            )
        )

    async with session_for_user(async_engine, b) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        b_names = sorted(
            r.name
            for r in await session.execute(
                sqlalchemy.text("SELECT name FROM watchlists")
            )
        )

    assert a_names == ["sess_e2e_a_row"]
    assert b_names == ["sess_e2e_b_row"]


@pytest.mark.integration
async def test_get_session_uses_lazy_global_engine(
    migrated_db: tuple[Engine, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_session`` builds the engine lazily from ``HORIZONS_DB_URL``
    and otherwise behaves identically to ``session_for_user``."""
    _, async_url = migrated_db
    monkeypatch.setenv("HORIZONS_DB_URL", async_url)
    monkeypatch.setattr(session_mod, "_engine", None)

    u = uuid.uuid4()  # not a real user; we just need the GUC echoed back
    async with get_session(u) as session:
        got = (
            await session.execute(
                sqlalchemy.text("SELECT current_setting('app.user_id', true)")
            )
        ).scalar_one()
    assert got == str(u)

    # Second bracket reuses the cached engine (covers the
    # ``_engine is not None`` branch in ``_get_engine``).
    v = uuid.uuid4()
    async with get_session(v) as session:
        got2 = (
            await session.execute(
                sqlalchemy.text("SELECT current_setting('app.user_id', true)")
            )
        ).scalar_one()
    assert got2 == str(v)

    # Dispose the lazy-cached engine and reset the module variable so
    # other tests don't see the test-scoped engine. ``getattr`` keeps
    # the private-attribute access out of pyright's lexical analysis.
    cached: AsyncEngine | None = getattr(session_mod, "_engine", None)
    if cached is not None:
        await cached.dispose()
    monkeypatch.setattr(session_mod, "_engine", None)

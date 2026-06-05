"""Async DB session bracket with per-request GUC binding.

The single sanctioned entry point for application code to talk to
Postgres. ``sqlalchemy.text()`` — the only imperative raw-SQL path —
is permitted **only inside this module** (see
``tests/test_raw_sql_isolation.py``). Models may use ``text()`` as a
declarative ``server_default=`` argument; that is a SQL expression
literal for schema generation, not raw-SQL execution, and the
architectural test allow-lists ``db/models/*.py`` accordingly.

See ``db/rls.md`` §Session contract for the bracket's responsibilities.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Final

import sqlalchemy
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.util import await_only

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncGenerator


_ENGINE_ENV_VAR: Final[str] = "HORIZONS_DB_URL"

_engine: AsyncEngine | None = None


def make_engine(url: str) -> AsyncEngine:
    """Build an ``AsyncEngine`` with the ``DISCARD ALL`` checkin handler.

    ``statement_cache_size=0`` disables asyncpg's client-side prepared
    statement cache. ``DISCARD ALL`` deallocates server-side prepared
    statements, which leaves asyncpg's cache stale and the next execute
    fails with ``InvalidSQLStatementNameError``. The cache costs
    perf only at re-prepare time; at demo scale the tradeoff is fine
    and keeps the simpler ``DISCARD ALL`` reset path.

    Use this in tests or anywhere you need a dedicated engine. For
    application code, ``get_session`` builds a lazy module-level engine
    from ``HORIZONS_DB_URL``.
    """
    engine = create_async_engine(
        url,
        future=True,
        connect_args={"statement_cache_size": 0},
    )
    _install_discard_all_on_checkin(engine)
    return engine


def _discard_all_on_checkin(dbapi_connection: Any, _record: Any) -> None:
    # Bypass the SQLAlchemy asyncpg adapter's cursor — its
    # implicit-transaction wrapping makes Postgres reject DISCARD ALL
    # with "cannot run inside a transaction block". Drive the underlying
    # asyncpg.Connection directly via the greenlet bridge so DISCARD ALL
    # runs against the bare session.
    await_only(dbapi_connection.driver_connection.execute("DISCARD ALL"))


def _install_discard_all_on_checkin(engine: AsyncEngine) -> None:
    event.listen(engine.sync_engine, "checkin", _discard_all_on_checkin)


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = make_engine(os.environ[_ENGINE_ENV_VAR])
    return _engine


@asynccontextmanager
async def session_for_user(engine: AsyncEngine, user_id: uuid.UUID) -> AsyncGenerator[AsyncSession]:
    """Yield a session inside a transaction with ``app.user_id`` bound.

    Commits on normal exit, rolls back on any exception. ``set_config``
    with ``is_local => true`` scopes the GUC to this transaction so
    per-request bleed is impossible on the happy path; ``DISCARD ALL``
    on pool checkin is the defence-in-depth second layer.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(
            sqlalchemy.text("SELECT set_config('app.user_id', :u, true)"),
            {"u": str(user_id)},
        )
        yield session


@asynccontextmanager
async def get_session(user_id: uuid.UUID) -> AsyncGenerator[AsyncSession]:
    """FastAPI-Depends-shaped session bracket against the global engine.

    The engine is constructed lazily from ``HORIZONS_DB_URL`` on first
    call. Tests can inject a dedicated engine via ``session_for_user``
    instead of monkeypatching the global.
    """
    async with session_for_user(_get_engine(), user_id) as session:
        yield session

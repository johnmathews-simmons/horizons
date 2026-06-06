"""Shared fixtures for horizons-core integration tests.

Provides:
- ``postgres_container`` — a session-scoped testcontainers Postgres 18
  instance (mirrors the root ``tests/conftest.py`` fixture; duplicated
  here because ``tests/conftest.py`` is a sibling tree that pytest does
  not auto-load for tests under ``packages/horizons-core/tests/``).
- ``migrated_engine`` — a function-scoped sync engine after Alembic has
  run to head; used for superuser-level seed inserts (only ``ingestion_worker``
  and the superuser have INSERT on corpus tables).
- ``admin_session`` — a function-scoped ``AsyncSession`` in an open
  transaction with ``SET LOCAL ROLE admin_bypass`` (BYPASSRLS; SELECT
  only via this role).
- ``api_app_session`` — a function-scoped ``AsyncSession`` in an open
  transaction with ``SET LOCAL ROLE api_app`` and ``app.user_id`` bound
  to a throwaway UUID.

Design notes:

- Async fixtures are function-scoped because asyncpg binds its engine to
  the event loop at first use; sharing a session-scoped engine across
  pytest-asyncio's function-scoped loops produces
  ``InterfaceError: another operation is in progress`` (same constraint
  noted in ``tests/isolation/conftest.py``).
- Inserts in test bodies must go through ``migrated_engine`` (sync,
  superuser) because ``admin_bypass`` has only SELECT on corpus tables;
  INSERT requires the superuser or ``ingestion_worker`` role.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy
from alembic import command
from alembic.config import Config
from horizons_core.db.session import make_engine, session_for_user
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
POSTGRES_IMAGE = "postgres:18-alpine"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Session-scoped Postgres 18 container."""
    if not _docker_available():
        pytest.skip("Docker is not running; skipping integration tests.")
    from testcontainers.postgres import PostgresContainer as _Pg

    container = _Pg(POSTGRES_IMAGE, driver="asyncpg")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def _migrated_urls(
    postgres_container: PostgresContainer,
) -> tuple[str, str]:
    """Run Alembic to head once and return ``(sync_url, async_url)``.

    Sets ``HORIZONS_DB_URL`` via ``os.environ`` directly (not
    ``monkeypatch``) because ``monkeypatch`` is function-scoped and
    cannot be requested from a session-scoped fixture.
    """
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    async_url = postgres_container.get_connection_url(driver="asyncpg")
    prev = os.environ.get("HORIZONS_DB_URL")
    os.environ["HORIZONS_DB_URL"] = sync_url
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    if prev is None:
        os.environ.pop("HORIZONS_DB_URL", None)
    else:
        os.environ["HORIZONS_DB_URL"] = prev
    return sync_url, async_url


@pytest.fixture
def migrated_engine(_migrated_urls: tuple[str, str]) -> Iterator[Engine]:
    """Function-scoped sync superuser engine for seed inserts."""
    sync_url, _ = _migrated_urls
    engine = create_engine(sync_url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def async_engine(
    _migrated_urls: tuple[str, str],
) -> AsyncIterator[AsyncEngine]:
    """Function-scoped async engine.

    Must be function-scoped: asyncpg binds to the event loop of the
    first test that uses it; a session-scoped engine shared across
    function-scoped test loops produces
    ``InterfaceError: another operation is in progress``.
    """
    _, async_url = _migrated_urls
    eng = make_engine(async_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def admin_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Admin-bypass async session (BYPASSRLS; SELECT on corpus tables)."""
    async with session_for_user(async_engine, uuid.uuid4()) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE admin_bypass"))
        yield session


@pytest_asyncio.fixture
async def api_app_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """api_app async session with a throwaway ``app.user_id`` GUC bound."""
    async with session_for_user(async_engine, uuid.uuid4()) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        yield session

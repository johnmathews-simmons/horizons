"""Shared fixtures for horizons-api integration tests.

Provides:
- ``postgres_container`` — session-scoped testcontainers Postgres 18.
- ``async_engine`` — function-scoped async engine (asyncpg).
- ``migrated_engine`` — function-scoped sync superuser engine after
  Alembic has run to head; used for seed inserts (only the superuser /
  ``ingestion_worker`` have INSERT on most tables).
- ``pg_session_admin`` — function-scoped ``AsyncSession`` under
  ``admin_bypass`` (BYPASSRLS; SELECT only on corpus tables).
- ``pg_session_api_app`` — function-scoped ``AsyncSession`` under
  ``api_app`` with a throwaway ``app.user_id`` GUC bound.
- ``admin_principal`` — a ``Principal`` whose ``user_id`` maps to a
  real ``users`` row with ``role='admin'`` (required because
  ``admin_access_log.admin_id`` is FK'd to ``users.id``).
- ``client_principal`` — a ``Principal`` with ``role='client'``;
  no ``users`` row is required because no audit writes occur for
  client callers.

Design notes mirror ``packages/horizons-core/tests/conftest.py``:
async fixtures are function-scoped because asyncpg binds its engine to
the event loop at first use.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy
from alembic import command
from alembic.config import Config
from horizons_core.core.auth import Principal, TokenKind, hash_password
from horizons_core.db.session import make_engine, session_for_user
from sqlalchemy import create_engine, text

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
async def pg_session_admin(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Admin-bypass async session (BYPASSRLS; SELECT on corpus tables)."""
    async with session_for_user(async_engine, uuid.uuid4()) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE admin_bypass"))
        yield session


@pytest_asyncio.fixture
async def pg_session_api_app(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """api_app async session with a throwaway ``app.user_id`` GUC bound."""
    async with session_for_user(async_engine, uuid.uuid4()) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        yield session


def _seed_user(engine: Engine, *, role: str) -> uuid.UUID:
    """Insert a user row and return its id (sync, superuser engine)."""
    pw_hash = hash_password("test-password")
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO users (email, password_hash, role) VALUES (:e, :p, :r) RETURNING id"),
            {
                "e": f"test-{uuid.uuid4()}@test.example",
                "p": pw_hash,
                "r": role,
            },
        ).scalar_one()


def _make_principal(user_id: uuid.UUID, *, role: str) -> Principal:
    """Build a minimal Principal — no JWT issued."""
    now = datetime.now(UTC)
    return Principal(
        user_id=user_id,
        role=role,
        kind=TokenKind.ACCESS,
        jti=uuid.uuid4(),
        issued_at=now,
        expires_at=now + timedelta(minutes=15),
    )


@pytest.fixture
def admin_principal(
    migrated_engine: Engine,
    _migrated_urls: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Principal:
    """A ``Principal`` with ``role='admin'`` backed by a real ``users`` row.

    ``admin_access_log.admin_id`` is FK'd to ``users.id``, so the
    audit-row tests need a real user. The ``HORIZONS_DB_URL`` env var
    is set to the async URL so that ``get_engine()`` in the dependency
    under test resolves against the test container.
    """
    _, async_url = _migrated_urls
    monkeypatch.setenv("HORIZONS_DB_URL", async_url)

    # Reset the module-level engine cache so it picks up the test URL.
    from horizons_core.db import session as session_mod

    monkeypatch.setattr(session_mod, "_engine", None)

    user_id = _seed_user(migrated_engine, role="admin")
    return _make_principal(user_id, role="admin")


@pytest.fixture
def client_principal(
    _migrated_urls: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Principal:
    """A ``Principal`` with ``role='client'``.

    No ``users`` row is needed: client callers never write audit rows,
    so no FK constraint is triggered. ``HORIZONS_DB_URL`` is still set
    (and the module-level engine cache cleared) so that ``get_session``
    inside the dependency can resolve the engine against the test
    container.
    """
    _, async_url = _migrated_urls
    monkeypatch.setenv("HORIZONS_DB_URL", async_url)

    from horizons_core.db import session as session_mod

    monkeypatch.setattr(session_mod, "_engine", None)

    return _make_principal(uuid.uuid4(), role="client")

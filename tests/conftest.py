"""Shared pytest fixtures for cross-package integration tests.

Provides:
- `postgres_container` — a session-scoped `testcontainers` Postgres 17
  instance, started once per test session.
- `engine` — a session-scoped SQLAlchemy async engine pointed at the
  container.

Tests using these fixtures should be marked `@pytest.mark.integration`
so they can be deselected with `pytest -m "not integration"`. The
fixtures themselves auto-skip when Docker is unreachable, so contributors
without Docker still get a green run — CI is the safety net.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy.ext.asyncio import AsyncEngine

POSTGRES_IMAGE = "postgres:17-alpine"


def _docker_available() -> bool:
    """True when a Docker daemon responds to `docker info`."""
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
    """Start a Postgres 17 container for the whole test session."""
    if not _docker_available():
        pytest.skip("Docker is not running; skipping integration tests.")
    container = PostgresContainer(POSTGRES_IMAGE, driver="asyncpg")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest_asyncio.fixture(scope="session")
async def engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    """Async SQLAlchemy engine pointed at the testcontainers Postgres."""
    eng = create_async_engine(postgres_container.get_connection_url(), future=True)
    try:
        yield eng
    finally:
        await eng.dispose()

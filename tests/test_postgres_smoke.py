"""Smoke test: the testcontainers Postgres substrate is reachable.

This is the only thing this test asserts. Real schema, migrations, and
repository behaviour land in Track 1; this exists so WU0.3's substrate
(testcontainers + asyncpg + SQLAlchemy async) is locked in and any future
breakage shows up immediately.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.mark.integration
async def test_postgres_select_1(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1 AS one"))
        row = result.one()
        assert row.one == 1

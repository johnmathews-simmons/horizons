"""Fake schedule table backing the WU3.0 worker-shape spike.

A throwaway stand-in for the real `document_poll_schedule` table that
WU3.1 will create. Same row shape, same SQL access pattern
(`SELECT ... FOR UPDATE SKIP LOCKED LIMIT N`), so both candidate
substrates exercise the same contention semantics a real worker will.

The spike runs against a testcontainers Postgres 18 instance, not
SQLite or an in-memory dict — SKIP LOCKED is the load-bearing part of
the comparison and only Postgres reproduces it faithfully.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg
    from testcontainers.postgres import PostgresContainer

SCHEMA = """
CREATE TABLE _spike_schedule (
    id BIGINT PRIMARY KEY,
    next_poll_at TIMESTAMPTZ NOT NULL,
    last_polled_at TIMESTAMPTZ,
    failure_count INT NOT NULL DEFAULT 0
);
CREATE INDEX ix_spike_due ON _spike_schedule (next_poll_at);
"""

# Claim due rows under a row-level lock that other workers (and other
# transactions in the same worker) skip past. This is the SQL the real
# WU3.3 claim loop will use verbatim, regardless of which substrate wins.
CLAIM_SQL = """
SELECT id FROM _spike_schedule
 WHERE next_poll_at <= now()
 ORDER BY next_poll_at
 FOR UPDATE SKIP LOCKED
 LIMIT $1
"""

# Reschedule the just-polled rows an hour out. Real worker uses the
# document's configured cadence_interval; here a constant is fine.
UPDATE_SQL = """
UPDATE _spike_schedule
   SET last_polled_at = now(),
       next_poll_at   = now() + interval '1 hour'
 WHERE id = ANY($1::bigint[])
"""


def container_dsn(pg: PostgresContainer) -> str:
    """asyncpg-compatible DSN from a testcontainers Postgres handle.

    testcontainers returns a SQLAlchemy-style URL
    (`postgresql+psycopg2://...`); asyncpg wants the plain scheme.
    """
    return pg.get_connection_url().replace("+psycopg2", "")


async def setup_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def seed_due_rows(pool: asyncpg.Pool, count: int) -> None:
    """Insert `count` rows all due to poll right now."""
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO _spike_schedule (id, next_poll_at) "
            "VALUES ($1, now() - interval '1 minute')",
            [(i,) for i in range(1, count + 1)],
        )


async def claim_batch(conn: asyncpg.Connection, batch_size: int) -> list[int]:
    """Run one SKIP LOCKED claim inside the caller's transaction."""
    rows = await conn.fetch(CLAIM_SQL, batch_size)
    return [r["id"] for r in rows]


async def reschedule(conn: asyncpg.Connection, ids: list[int]) -> None:
    await conn.execute(UPDATE_SQL, ids)


async def remaining_due(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM _spike_schedule WHERE next_poll_at <= now()"
        )
        return int(row["n"]) if row else 0

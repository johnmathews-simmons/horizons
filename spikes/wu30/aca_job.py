"""Candidate B: scheduled ACA Job — run-once-and-exit shape.

Shape: orchestrator (ACA Job, cron, k8s CronJob, anything) invokes the
process on a schedule. The process opens a pool, drains every currently
due row, prints a report, exits 0. Between invocations there is no
worker — no idle CPU, no liveness probe, no SIGTERM handling.

Operationally this needs:
- Nothing beyond a clean exit code and stdout that the orchestrator
  scrapes. No signal handling (the process is short-lived; the
  orchestrator kills it if it overruns).
- A scheduler outside the process. ACA Jobs handle this natively; on a
  developer's laptop it's just `python -m spikes.wu30.aca_job` in a
  loop or a `watch` command.

Process model = invoke-then-exit. No idle compute; reaction latency is
bounded below by the orchestrator's minimum schedule granularity
(ACA Jobs: 1 minute).
"""

from __future__ import annotations

import asyncio
import sys

import asyncpg
from testcontainers.postgres import PostgresContainer

from spikes.wu30.fake_schedule import (
    claim_batch,
    container_dsn,
    remaining_due,
    reschedule,
    seed_due_rows,
    setup_schema,
)

BATCH_SIZE = 10


async def drain_once(pool: asyncpg.Pool) -> int:
    """Claim and process batches until the queue is empty, then return."""
    processed = 0
    while True:
        async with pool.acquire() as conn, conn.transaction():
            ids = await claim_batch(conn, BATCH_SIZE)
            if not ids:
                return processed
            await asyncio.sleep(0)  # placeholder for real per-row Lawstronaut poll
            await reschedule(conn, ids)
            processed += len(ids)


async def main(seed_n: int = 100) -> int:
    with PostgresContainer("postgres:18-alpine") as pg:
        pool = await asyncpg.create_pool(container_dsn(pg), min_size=2, max_size=4)
        if pool is None:
            print("pool creation failed", file=sys.stderr)
            return 2
        try:
            await setup_schema(pool)
            await seed_due_rows(pool, seed_n)
            print(f"seeded={seed_n} remaining_due_before={await remaining_due(pool)}")
            processed = await drain_once(pool)
            print(f"processed={processed} remaining_due_after={await remaining_due(pool)}")
        finally:
            await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Candidate A: long-running asyncio container.

Shape: one container, one process, one event loop, perpetual claim loop
ticking every `TICK_SECONDS`. The container's liveness is the worker's
liveness; the orchestrator does not restart it between batches.

Operationally this needs:
- A SIGTERM handler so the loop drains in-flight work before exit.
- A liveness signal (here just a timestamp file; real worker would expose
  `/healthz` over HTTP so ACA can probe it).
- A reconnect strategy when the pool falls over (omitted from the spike;
  asyncpg's pool already reconnects on transient errors but the surrounding
  loop has to tolerate `OperationalError` etc.).

Process model = always-on. Idle compute is the price of low-latency
reaction to a row becoming due.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

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
TICK_SECONDS = 0.05
LIVENESS_FILE = Path("/tmp/horizons-wu30-asyncio.alive")  # noqa: S108


async def claim_tick(pool: asyncpg.Pool) -> int:
    """One iteration: claim a batch, "process" (no-op), reschedule, commit."""
    async with pool.acquire() as conn, conn.transaction():
        ids = await claim_batch(conn, BATCH_SIZE)
        if not ids:
            return 0
        await asyncio.sleep(0)  # placeholder for real per-row Lawstronaut poll
        await reschedule(conn, ids)
        return len(ids)


async def run_loop(pool: asyncpg.Pool, stop: asyncio.Event) -> int:
    """Tick until `stop` is set OR the queue stays empty for one tick."""
    processed = 0
    empty_ticks = 0
    while not stop.is_set():
        LIVENESS_FILE.touch()
        n = await claim_tick(pool)
        processed += n
        empty_ticks = empty_ticks + 1 if n == 0 else 0
        if empty_ticks >= 1:
            # Spike-only exit; a real worker would keep ticking forever.
            break
        await asyncio.sleep(TICK_SECONDS)
    return processed


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

            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)

            processed = await run_loop(pool, stop)
            print(f"processed={processed} remaining_due_after={await remaining_due(pool)}")
        finally:
            await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

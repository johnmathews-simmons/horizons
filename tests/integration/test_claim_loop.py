"""Integration tests for the WU3.3 schedule claim loop.

Exercises the substrate decisions fixed by
``docs/adrs/0001-worker-shape.md``:

- ``SELECT ... FOR UPDATE SKIP LOCKED`` prevents two ticks from
  claiming the same schedule row.
- A failing poll bumps ``failure_count``; the call that crosses the
  threshold writes one ``ingestion_incident`` and the row stays parked
  on subsequent ticks.
- A successful poll advances ``next_poll_at`` by the cadence and
  resets ``failure_count``.
- ``ClaimLoop.run(shutdown)`` finishes the in-flight tick on SIGTERM
  and exits cleanly.

All tests use the session-scoped ``postgres_container`` from
``tests/conftest.py`` via the ``migrated_db`` / ``pool`` fixtures in
the sibling ``conftest.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

import pytest
from horizons_ingestion.config import ClaimLoopConfig
from horizons_ingestion.loop import ClaimLoop, LoopState, noop_poll

from .conftest import fetch_incidents, fetch_schedule_row, seed_schedule

if TYPE_CHECKING:
    import uuid

    import asyncpg
    from horizons_ingestion.loop import PoolConnection

    from .conftest import MigratedDb


pytestmark = pytest.mark.integration


def _cfg(**overrides: object) -> ClaimLoopConfig:
    """ClaimLoopConfig with test-friendly defaults."""
    defaults: dict[str, object] = {
        "db_url": "unused-in-tests",
        "tick_interval_s": 0.0,
        "batch_size": 10,
        "failure_threshold": 5,
        "healthz_stale_after_s": 5.0,
        "healthz_host": "127.0.0.1",
        "healthz_port": 0,
        "pool_min": 2,
        "pool_max": 4,
    }
    defaults.update(overrides)
    return ClaimLoopConfig(**defaults)  # type: ignore[arg-type]


async def test_tick_with_noop_poll_marks_rows_polled_ok(
    migrated_db: MigratedDb,
    pool: asyncpg.Pool,
) -> None:
    """A successful poll advances next_poll_at and resets failure_count."""
    [doc_id] = seed_schedule(migrated_db.sync_engine, n_due=1, failure_count=2)
    loop = ClaimLoop(pool=pool, poll=noop_poll, config=_cfg(), state=LoopState.new())

    processed = await loop.tick()

    assert processed == 1
    row = fetch_schedule_row(migrated_db.sync_engine, doc_id)
    assert row["failure_count"] == 0
    assert row["last_polled_at"] is not None
    assert row["next_poll_at"] is not None  # bumped forward by cadence


async def test_skip_locked_prevents_double_claim(
    migrated_db: MigratedDb,
    pool: asyncpg.Pool,
) -> None:
    """Two concurrent ticks split the due rows; no row processed twice."""
    seed_schedule(migrated_db.sync_engine, n_due=4)

    entered = asyncio.Event()
    release = asyncio.Event()
    a_seen: list[uuid.UUID] = []
    b_seen: list[uuid.UUID] = []

    async def gated_poll(_conn: PoolConnection, doc_id: uuid.UUID) -> None:
        a_seen.append(doc_id)
        if not entered.is_set():
            entered.set()
        await release.wait()

    async def quick_poll(_conn: PoolConnection, doc_id: uuid.UUID) -> None:
        b_seen.append(doc_id)

    loop_a = ClaimLoop(pool=pool, poll=gated_poll, config=_cfg(), state=LoopState.new())
    loop_b = ClaimLoop(pool=pool, poll=quick_poll, config=_cfg(), state=LoopState.new())

    task_a = asyncio.create_task(loop_a.tick())
    await entered.wait()  # tick A holds 4 row locks
    b_processed = await loop_b.tick()  # B's SELECT skips all 4
    release.set()
    a_processed = await task_a

    assert b_processed == 0
    assert b_seen == []
    assert a_processed == 4
    assert len(set(a_seen)) == 4


async def test_failing_poll_bumps_failure_count_below_threshold(
    migrated_db: MigratedDb,
    pool: asyncpg.Pool,
) -> None:
    """One failed poll: failure_count = 1, no incident, still claimable."""
    [doc_id] = seed_schedule(migrated_db.sync_engine, n_due=1)

    async def failing_poll(_conn: PoolConnection, _doc_id: uuid.UUID) -> None:
        raise RuntimeError("upstream 500")

    loop = ClaimLoop(pool=pool, poll=failing_poll, config=_cfg(), state=LoopState.new())
    processed = await loop.tick()

    assert processed == 1
    row = fetch_schedule_row(migrated_db.sync_engine, doc_id)
    assert row["failure_count"] == 1
    assert fetch_incidents(migrated_db.sync_engine, doc_id) == []


async def test_kill_switch_parks_row_and_writes_incident_at_threshold(
    migrated_db: MigratedDb,
    pool: asyncpg.Pool,
) -> None:
    """Six consecutive failures: incident written on the 6th; subsequent ticks skip."""
    [doc_id] = seed_schedule(migrated_db.sync_engine, n_due=1)

    async def failing_poll(_conn: PoolConnection, _doc_id: uuid.UUID) -> None:
        raise RuntimeError("upstream persistent failure")

    loop = ClaimLoop(pool=pool, poll=failing_poll, config=_cfg(), state=LoopState.new())

    for _ in range(6):
        await loop.tick()

    row = fetch_schedule_row(migrated_db.sync_engine, doc_id)
    assert row["failure_count"] == 6
    incidents = fetch_incidents(migrated_db.sync_engine, doc_id)
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["error_class"] == "parked"
    raw = incident["payload"]
    payload: dict[str, object] = (
        json.loads(raw) if isinstance(raw, str) else cast("dict[str, object]", raw)
    )
    assert "upstream persistent failure" in str(payload.get("message", ""))

    # Subsequent tick must skip the parked row.
    processed = await loop.tick()
    assert processed == 0


async def test_run_drains_in_flight_tick_on_shutdown(
    migrated_db: MigratedDb,
    pool: asyncpg.Pool,
) -> None:
    """``run(shutdown)`` finishes the in-flight tick before exiting."""
    [doc_id] = seed_schedule(migrated_db.sync_engine, n_due=1)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def gated_poll(_conn: PoolConnection, _doc_id: uuid.UUID) -> None:
        entered.set()
        await release.wait()

    loop = ClaimLoop(pool=pool, poll=gated_poll, config=_cfg(), state=LoopState.new())
    shutdown = asyncio.Event()

    task = asyncio.create_task(loop.run(shutdown))
    await entered.wait()  # in-flight tick is parked in the poll
    shutdown.set()
    # The in-flight tick has not committed yet — failure_count must
    # still be 0 in a fresh connection because of READ COMMITTED.
    # Release the poll; the tick should now commit and the loop exit.
    release.set()
    await asyncio.wait_for(task, timeout=2.0)

    row = fetch_schedule_row(migrated_db.sync_engine, doc_id)
    assert row["last_polled_at"] is not None
    assert row["failure_count"] == 0


async def test_run_exits_promptly_when_idle_and_shutdown(
    pool: asyncpg.Pool,
    migrated_db: MigratedDb,  # noqa: ARG001  # ensure migrations applied
) -> None:
    """An idle (no due rows) loop exits when ``shutdown`` is set."""
    loop = ClaimLoop(pool=pool, poll=noop_poll, config=_cfg(), state=LoopState.new())
    shutdown = asyncio.Event()

    task = asyncio.create_task(loop.run(shutdown))
    await asyncio.sleep(0.05)
    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)

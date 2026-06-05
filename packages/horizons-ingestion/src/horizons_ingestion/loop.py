"""Schedule claim loop — the ingestion worker's hot path.

See ``loop.md`` for the full design. In summary:

- Every tick acquires a pooled connection, opens a transaction, claims
  a batch via ``SELECT ... FOR UPDATE SKIP LOCKED``, calls the injected
  ``PollFn`` for each claimed row, and updates the schedule row.
- A row whose ``failure_count`` crosses the threshold writes one
  ``ingestion_incident`` with ``error_class = 'parked'``. The claim
  query filters parked rows out on subsequent ticks.
- ``ClaimLoop.run(shutdown)`` polls an ``asyncio.Event`` between ticks
  and exits cleanly once the in-flight tick commits.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid as _uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import asyncpg

if TYPE_CHECKING:
    from horizons_ingestion.config import ClaimLoopConfig
    from horizons_ingestion.health import LoopHealth


# ``PoolConnection`` accepts either a bare ``Connection`` or the proxy
# the pool hands out. asyncpg's stubs distinguish the two but both
# satisfy the same query surface (``execute``, ``fetch``, ``fetchval``).
type PoolConnection = (
    asyncpg.Connection[asyncpg.Record] | asyncpg.pool.PoolConnectionProxy[asyncpg.Record]
)
type PollFn = Callable[[PoolConnection, _uuid.UUID], Awaitable[None]]


_log = logging.getLogger(__name__)

CLAIM_SQL: Final = """
SELECT document_id FROM document_poll_schedule
 WHERE next_poll_at <= now() AND failure_count <= $1
 ORDER BY next_poll_at
 FOR UPDATE SKIP LOCKED LIMIT $2
"""

MARK_OK_SQL: Final = """
UPDATE document_poll_schedule
   SET last_polled_at = now(),
       next_poll_at = now() + cadence_interval,
       failure_count = 0
 WHERE document_id = $1
"""

MARK_FAIL_SQL: Final = """
UPDATE document_poll_schedule
   SET last_polled_at = now(),
       failure_count = failure_count + 1
 WHERE document_id = $1
RETURNING failure_count
"""

INSERT_INCIDENT_SQL: Final = """
INSERT INTO ingestion_incident (document_id, error_class, payload)
VALUES ($1, $2, $3::jsonb)
"""


async def noop_poll(_conn: PoolConnection, _document_id: _uuid.UUID) -> None:
    """Stub poll body used until WU3.4 ships the real per-document transaction."""
    return None


@dataclass(slots=True)
class LoopState:
    """Per-loop runtime counters. Not configuration — the loop mutates them."""

    last_tick_at: datetime | None = None
    ticks: int = 0
    rows_processed: int = 0
    incidents_written: int = 0

    @classmethod
    def new(cls) -> LoopState:
        return cls()


class ClaimLoop:
    """The asyncio claim loop spec'd in ADR-0001 §Confirmation."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        poll: PollFn,
        config: ClaimLoopConfig,
        *,
        state: LoopState | None = None,
        health: LoopHealth | None = None,
    ) -> None:
        self.pool = pool
        self.poll = poll
        self.cfg = config
        self.state = state if state is not None else LoopState.new()
        self.health = health

    async def run(self, shutdown: asyncio.Event) -> None:
        """Drive ticks until ``shutdown`` is set.

        The in-flight tick is allowed to finish — ADR-0001 requires
        SIGTERM-drain. The sleep between ticks is interruptible: the
        loop reacts within ``tick_interval_s`` (default 50 ms) of
        ``shutdown.set()``.
        """
        _log.info(
            "claim_loop starting (tick_interval_s=%s, batch_size=%s, failure_threshold=%s)",
            self.cfg.tick_interval_s,
            self.cfg.batch_size,
            self.cfg.failure_threshold,
        )
        try:
            while not shutdown.is_set():
                try:
                    await self.tick()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:  # noqa: BLE001
                    # The poll body's exceptions are caught and recorded
                    # inside ``tick``. Anything bubbling out here is a
                    # claim-side failure (lost connection, server gone).
                    # Log, sleep one tick, and try again.
                    _log.exception("claim tick failed; will retry")
                if shutdown.is_set():
                    break
                if self.cfg.tick_interval_s > 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            shutdown.wait(),
                            timeout=self.cfg.tick_interval_s,
                        )
        finally:
            _log.info(
                "claim_loop stopped (ticks=%d, rows_processed=%d, incidents_written=%d)",
                self.state.ticks,
                self.state.rows_processed,
                self.state.incidents_written,
            )

    async def tick(self) -> int:
        """Claim up to ``batch_size`` due rows, poll each, commit. Returns the row count."""
        async with self.pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            try:
                rows = await conn.fetch(
                    CLAIM_SQL,
                    self.cfg.failure_threshold,
                    self.cfg.batch_size,
                )
                for row in rows:
                    document_id = row["document_id"]
                    try:
                        await self.poll(conn, document_id)
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        await self._record_failure(conn, document_id, exc)
                    else:
                        await conn.execute(MARK_OK_SQL, document_id)
            except BaseException:
                await tx.rollback()
                raise
            else:
                await tx.commit()
        processed = len(rows)
        self.state.ticks += 1
        self.state.rows_processed += processed
        now = datetime.now(UTC)
        self.state.last_tick_at = now
        if self.health is not None:
            self.health.touch(now)
        return processed

    async def _record_failure(
        self,
        conn: PoolConnection,
        document_id: _uuid.UUID,
        exc: BaseException,
    ) -> None:
        new_count = await conn.fetchval(MARK_FAIL_SQL, document_id)
        if new_count is None:  # pragma: no cover  # row vanished mid-tick
            return
        if new_count > self.cfg.failure_threshold:
            payload = json.dumps(
                {
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                    "failure_count": int(new_count),
                }
            )
            await conn.execute(
                INSERT_INCIDENT_SQL,
                document_id,
                "parked",
                payload,
            )
            self.state.incidents_written += 1
            _log.warning(
                "schedule entry parked: document_id=%s failure_count=%d error=%s",
                document_id,
                new_count,
                exc,
            )

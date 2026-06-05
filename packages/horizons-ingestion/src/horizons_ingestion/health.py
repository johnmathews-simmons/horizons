"""``/healthz`` over a tiny aiohttp surface.

ADR-0001 requires a liveness probe so Azure Container Apps can decide
when to restart the replica. The probe checks loop liveness — last-tick
recency — and does not hit Postgres: the loop itself exercises the DB
every tick and a stalled loop is exactly the failure mode the probe
catches. A separate DB-ping path would add latency to a probe that
gains no signal not already present.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from collections.abc import Callable


class LoopHealth:
    """Tracks the loop's last-tick timestamp and answers ``is_healthy``.

    Mutable so the loop can call ``touch()`` after every tick without
    rebuilding the aiohttp app. Single-writer (the loop) / multi-reader
    (probe handlers); the GIL is sufficient for a single timestamp on
    one event loop.
    """

    __slots__ = ("_last_tick_at", "stale_after_s")

    def __init__(self, stale_after_s: float) -> None:
        self.stale_after_s = stale_after_s
        self._last_tick_at: datetime | None = None

    @property
    def last_tick_at(self) -> datetime | None:
        return self._last_tick_at

    def touch(self, now: datetime) -> None:
        self._last_tick_at = now

    def is_healthy(self, now: datetime) -> bool:
        if self._last_tick_at is None:
            return False
        return (now - self._last_tick_at).total_seconds() <= self.stale_after_s


def _utcnow() -> datetime:
    return datetime.now(UTC)


def build_healthz_app(
    health: LoopHealth,
    *,
    clock: Callable[[], datetime] = _utcnow,
) -> web.Application:
    """Return an ``aiohttp.web.Application`` exposing ``GET /healthz``.

    ``clock`` is injected for tests so we don't have to sleep.
    """

    async def handler(_request: web.Request) -> web.Response:
        now = clock()
        if health.is_healthy(now):
            return web.Response(text="ok")
        last = health.last_tick_at
        if last is None:
            return web.Response(status=503, text="stalled: no tick yet")
        age = (now - last).total_seconds()
        return web.Response(status=503, text=f"stalled {age:.1f}s")

    app = web.Application()
    app.router.add_get("/healthz", handler)
    return app

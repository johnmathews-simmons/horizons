"""Unit tests for the ``/healthz`` aiohttp surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiohttp.test_utils import TestClient, TestServer
from horizons_ingestion.health import LoopHealth, build_healthz_app


async def test_healthz_returns_200_when_loop_recently_ticked() -> None:
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    health = LoopHealth(stale_after_s=5.0)
    health.touch(now)

    app = build_healthz_app(health, clock=lambda: now + timedelta(seconds=1))
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        body = await resp.text()
        status = resp.status

    assert status == 200
    assert body == "ok"


async def test_healthz_returns_503_when_loop_stalled() -> None:
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)
    health = LoopHealth(stale_after_s=5.0)
    health.touch(now)

    app = build_healthz_app(health, clock=lambda: now + timedelta(seconds=42))
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        body = await resp.text()
        status = resp.status

    assert status == 503
    assert "stalled" in body


async def test_healthz_returns_503_before_first_tick() -> None:
    """A freshly-constructed LoopHealth has not ticked yet — probe must fail."""
    health = LoopHealth(stale_after_s=5.0)
    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)

    app = build_healthz_app(health, clock=lambda: now)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        status = resp.status

    assert status == 503

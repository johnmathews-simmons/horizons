"""Ingestion worker entry point: ``python -m horizons_ingestion``.

ADR-0001 §Confirmation requires:

- Single ``python -m horizons_ingestion`` entry point.
- Container stays alive between batches.
- SIGTERM drains in-flight work before the container exits.
- ``/healthz`` returns 200 while the loop is running.

This module wires those four invariants together. The substrate-specific
glue (pool init, aiohttp serve, signal handlers) lives here; the
business of claiming and polling rows lives in ``loop.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from typing import TYPE_CHECKING

import asyncpg
from aiohttp import web

from horizons_ingestion.config import ClaimLoopConfig, asyncpg_dsn
from horizons_ingestion.health import LoopHealth, build_healthz_app
from horizons_ingestion.loop import ClaimLoop, LoopState, noop_poll

if TYPE_CHECKING:
    from collections.abc import Iterable


_log = logging.getLogger("horizons_ingestion")


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown: asyncio.Event,
    signals: Iterable[signal.Signals] = (signal.SIGTERM, signal.SIGINT),
) -> None:
    for sig in signals:
        # Windows / non-Unix event loops don't implement signal handlers.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, shutdown.set)


async def _run(cfg: ClaimLoopConfig) -> None:
    pool = await asyncpg.create_pool(
        dsn=asyncpg_dsn(cfg.db_url),
        min_size=cfg.pool_min,
        max_size=cfg.pool_max,
    )

    health = LoopHealth(stale_after_s=cfg.healthz_stale_after_s)
    app = build_healthz_app(health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.healthz_host, cfg.healthz_port)
    await site.start()
    _log.info("healthz listening on %s:%s", cfg.healthz_host, cfg.healthz_port)

    state = LoopState.new()
    claim_loop = ClaimLoop(
        pool=pool,
        poll=noop_poll,
        config=cfg,
        state=state,
        health=health,
    )

    shutdown = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), shutdown)

    try:
        await claim_loop.run(shutdown)
    finally:
        await runner.cleanup()
        await pool.close()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("HORIZONS_INGESTION_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = ClaimLoopConfig.from_env(os.environ)
    asyncio.run(_run(cfg))


if __name__ == "__main__":  # pragma: no cover  # exercised via container entrypoint
    main()

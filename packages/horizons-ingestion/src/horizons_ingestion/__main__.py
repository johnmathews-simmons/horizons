"""Ingestion worker entry point: ``python -m horizons_ingestion``.

ADR-0001 §Confirmation requires:

- Single ``python -m horizons_ingestion`` entry point.
- Container stays alive between batches.
- SIGTERM drains in-flight work before the container exits.
- ``/healthz`` returns 200 while the loop is running.

This module wires those four invariants together, the WU3.4 per-document
poll body, and the orphan-blob sweep. The substrate-specific glue (pool
init, aiohttp serve, signal handlers, Azure credential) lives here; the
business of claiming and polling rows lives in ``loop.py``; the
business of one poll lives in ``poll.py``; the business of reclaiming
orphan blobs lives in ``sweep.py``.

Required environment to deploy:

- ``HORIZONS_DB_URL`` — Postgres URL (SQLAlchemy or asyncpg shape).
- ``HORIZONS_INGESTION_BLOB_ACCOUNT_URL`` — e.g. ``https://acct.blob.core.windows.net``.
- ``LAWSTRONAUT_EMAIL`` / ``LAWSTRONAUT_PASSWORD`` — auth credentials.

Optional knobs are documented in ``loop.md`` §Configuration.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import signal
from typing import TYPE_CHECKING

import asyncpg
from aiohttp import web
from azure.identity.aio import DefaultAzureCredential
from horizons_core.core.lawstronaut import Credentials, LawstronautClient
from pydantic import SecretStr

from horizons_ingestion.blob import AzureBlobStore
from horizons_ingestion.config import ClaimLoopConfig, asyncpg_dsn
from horizons_ingestion.health import LoopHealth, build_healthz_app
from horizons_ingestion.loop import ClaimLoop, LoopState
from horizons_ingestion.poll import poll_document
from horizons_ingestion.sweep import SweepLoop

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


def _read_lawstronaut_credentials(env: dict[str, str]) -> Credentials:
    email = env.get("LAWSTRONAUT_EMAIL")
    password = env.get("LAWSTRONAUT_PASSWORD")
    if not email or not password:
        raise KeyError("LAWSTRONAUT_EMAIL / LAWSTRONAUT_PASSWORD")
    return Credentials(email=email, password=SecretStr(password))


async def _run(cfg: ClaimLoopConfig) -> None:
    if cfg.blob_account_url is None:
        raise KeyError("HORIZONS_INGESTION_BLOB_ACCOUNT_URL")

    pool = await asyncpg.create_pool(
        dsn=asyncpg_dsn(cfg.db_url),
        min_size=cfg.pool_min,
        max_size=cfg.pool_max,
    )

    credentials = _read_lawstronaut_credentials(dict(os.environ))

    health = LoopHealth(stale_after_s=cfg.healthz_stale_after_s)
    app = build_healthz_app(health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.healthz_host, cfg.healthz_port)
    await site.start()
    _log.info("healthz listening on %s:%s", cfg.healthz_host, cfg.healthz_port)

    azure_credential = DefaultAzureCredential()
    async with (
        LawstronautClient(credentials=credentials) as client,
        AzureBlobStore(
            account_url=cfg.blob_account_url,
            container=cfg.blob_container,
            credential=azure_credential,
        ) as blob_store,
    ):
        poll = functools.partial(
            poll_document,
            client=client,
            blob_store=blob_store,
            blob_container=cfg.blob_container,
        )

        state = LoopState.new()
        claim_loop = ClaimLoop(
            pool=pool,
            poll=poll,
            config=cfg,
            state=state,
            health=health,
        )
        sweep_loop = SweepLoop(
            pool=pool,
            blob_store=blob_store,
            container=cfg.blob_container,
            interval_s=cfg.sweep_interval_s,
        )

        shutdown = asyncio.Event()
        _install_signal_handlers(asyncio.get_running_loop(), shutdown)

        try:
            await asyncio.gather(
                claim_loop.run(shutdown),
                sweep_loop.run(shutdown),
            )
        finally:
            await runner.cleanup()
            await pool.close()
            await azure_credential.close()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("HORIZONS_INGESTION_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = ClaimLoopConfig.from_env(os.environ)
    asyncio.run(_run(cfg))


if __name__ == "__main__":  # pragma: no cover  # exercised via container entrypoint
    main()

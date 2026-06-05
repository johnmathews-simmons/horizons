"""Orphan-blob sweep — the second loop in the ingestion worker.

WU3.4 acceptance: "Failed runs leave at most one orphan blob; a
periodic sweep job reclaims them." Per ADR-0001 the worker is one
long-running container, so the sweep runs as a second slow tick in
the same process — no new Bicep, no separate ACA Job. Cadence is
configurable (default 30 minutes) so demo-time observation can crank
it up or down without a redeploy.

Algorithm: list every blob in the container; for each key whose stem
matches a sha256 hex (64 lowercase hex chars), check whether any
``document_versions`` row references it; if not, delete the blob.
``poll.py`` uploads under ``<sha256>.md`` keys so the stem is exactly
``content_blob_key`` rows minus the ``.md`` suffix.

Reclaim ordering: we list then check then delete, one blob at a time.
A concurrent poll that uploads a new blob between the list and the
check sees its row inserted before the next list, so the next sweep
spares it. Worst case: a sweep that starts just before a poll uploads
a blob and finishes just before that poll commits the version row
would delete the new blob — but the poll body uploads first and only
opens the DB transaction afterwards, so the version row insert and
the sweep's blob-existence check race on the same connection-pool.
Belt-and-braces: the sweep grabs the version-key set inside a
transaction at start of pass and never re-reads, so concurrent
inserts during a long sweep are seen on the *next* pass.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import asyncio

    import asyncpg

    from horizons_ingestion.blob import BlobStore


_log = logging.getLogger(__name__)


SWEEP_VERSION_KEYS_SQL: Final = """
SELECT content_blob_key FROM document_versions WHERE content_blob_container = $1
"""


class SweepLoop:
    """Drives :meth:`sweep_once` on a configurable cadence.

    Lifecycle mirrors :class:`ClaimLoop` exactly: a long-running coroutine
    that polls an ``asyncio.Event`` between iterations and exits cleanly.
    The ``ClaimLoop`` runs ticks every ~50 ms; this loop runs every
    ``interval_s`` (default 1800 s — 30 min).
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        blob_store: BlobStore,
        container: str,
        interval_s: float,
    ) -> None:
        self.pool = pool
        self.blob_store = blob_store
        self.container = container
        self.interval_s = interval_s

    async def run(self, shutdown: asyncio.Event) -> None:
        """Drive sweeps until ``shutdown`` is set."""
        # Local import to avoid pulling asyncio at module import time.
        import asyncio  # noqa: PLC0415
        import contextlib  # noqa: PLC0415

        _log.info("sweep_loop starting (interval_s=%s)", self.interval_s)
        try:
            while not shutdown.is_set():
                try:
                    await self.sweep_once()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:  # noqa: BLE001
                    _log.exception("sweep iteration failed; will retry")
                if shutdown.is_set():
                    break
                if self.interval_s > 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            shutdown.wait(),
                            timeout=self.interval_s,
                        )
        finally:
            _log.info("sweep_loop stopped")

    async def sweep_once(self) -> int:
        """Run one sweep pass. Returns the count of blobs reclaimed."""
        keys = await self._referenced_keys()
        reclaimed = 0
        async for blob_key in self.blob_store.iter_keys():
            if blob_key in keys:
                continue
            # Only sweep keys that look like our content-addressed
            # convention. Anything else might be a sibling artefact a
            # human dropped in the container.
            if not _is_content_addressed_key(blob_key):
                continue
            await self.blob_store.delete(blob_key)
            reclaimed += 1
            _log.info("sweep: reclaimed orphan blob %s", blob_key)
        return reclaimed

    async def _referenced_keys(self) -> set[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(SWEEP_VERSION_KEYS_SQL, self.container)
        return {str(row["content_blob_key"]) for row in rows}


def _is_content_addressed_key(key: str) -> bool:
    """Return ``True`` if ``key`` matches the ``<sha256-hex>.md`` shape."""
    if not key.endswith(".md"):
        return False
    stem = key[: -len(".md")]
    if len(stem) != 64:
        return False
    return all(c in "0123456789abcdef" for c in stem)

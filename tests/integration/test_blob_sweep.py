"""Integration tests for the orphan-blob sweep (WU3.4).

Drives :class:`SweepLoop.sweep_once` against a migrated testcontainers
Postgres + an in-memory :class:`MemoryBlobStore`. Asserts:

- Referenced blobs (those named in ``document_versions.content_blob_key``)
  are spared.
- Orphan blobs (matching ``<sha256>.md`` shape but unreferenced) are
  deleted.
- Blobs that don't match the content-addressed shape are spared.
- A run that finds nothing to reclaim returns ``0``.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from horizons_ingestion.blob import MemoryBlobStore
from horizons_ingestion.sweep import SweepLoop

if TYPE_CHECKING:
    import asyncpg
    from sqlalchemy import Engine

    from .conftest import MigratedDb


pytestmark = pytest.mark.integration


def _insert_doc_and_version(
    sync_engine: Engine,
    *,
    blob_key: str,
    sha: bytes,
    container: str = "originals",
) -> uuid.UUID:
    from sqlalchemy import text  # noqa: PLC0415

    with sync_engine.begin() as conn:
        doc_id = conn.execute(
            text(
                "INSERT INTO documents "
                "(jurisdiction, sector, lawstronaut_document_id, title) "
                "VALUES (:j, :s, :lid, :t) RETURNING id"
            ),
            {
                "j": "IE",
                "s": "BANKING",
                "lid": f"sweep-{uuid.uuid4()}",
                "t": "Sweep fixture",
            },
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO document_versions "
                "(document_id, version_label, version_no, valid_from, "
                "publication_date, content_blob_container, content_blob_key, "
                "content_sha256, content_bytes) "
                "VALUES (:d, 'v1', 1, :vf, :pd, :c, :k, :h, :b)"
            ),
            {
                "d": doc_id,
                "vf": datetime.now(UTC),
                "pd": datetime.now(UTC) - timedelta(days=1),
                "c": container,
                "k": blob_key,
                "h": sha,
                "b": 16,
            },
        )
    return doc_id


async def test_sweep_reclaims_orphan_and_spares_referenced(
    migrated_db: MigratedDb,
    pool: asyncpg.Pool,
) -> None:
    referenced_body = b"referenced-body"
    orphan_body = b"orphan-body"
    referenced_sha = hashlib.sha256(referenced_body).digest()
    orphan_sha = hashlib.sha256(orphan_body).digest()

    referenced_key = referenced_sha.hex() + ".md"
    orphan_key = orphan_sha.hex() + ".md"

    _insert_doc_and_version(migrated_db.sync_engine, blob_key=referenced_key, sha=referenced_sha)

    blob_store = MemoryBlobStore()
    await blob_store.put(referenced_key, referenced_body)
    await blob_store.put(orphan_key, orphan_body)

    sweep = SweepLoop(pool=pool, blob_store=blob_store, container="originals", interval_s=0.0)
    reclaimed = await sweep.sweep_once()

    assert reclaimed == 1
    assert await blob_store.exists(referenced_key) is True
    assert await blob_store.exists(orphan_key) is False


async def test_sweep_ignores_non_content_addressed_keys(
    migrated_db: MigratedDb,  # noqa: ARG001  # ensure migrations
    pool: asyncpg.Pool,
) -> None:
    blob_store = MemoryBlobStore()
    await blob_store.put("readme.md", b"a hand-dropped file")
    await blob_store.put("manifest.json", b"{}")

    sweep = SweepLoop(pool=pool, blob_store=blob_store, container="originals", interval_s=0.0)
    reclaimed = await sweep.sweep_once()

    assert reclaimed == 0
    assert await blob_store.exists("readme.md") is True
    assert await blob_store.exists("manifest.json") is True


async def test_sweep_empty_container_is_a_noop(
    migrated_db: MigratedDb,  # noqa: ARG001
    pool: asyncpg.Pool,
) -> None:
    blob_store = MemoryBlobStore()
    sweep = SweepLoop(pool=pool, blob_store=blob_store, container="originals", interval_s=0.0)
    assert await sweep.sweep_once() == 0

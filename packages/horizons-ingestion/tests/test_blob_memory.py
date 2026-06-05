"""Unit tests for :class:`MemoryBlobStore`.

Verifies the contract the production :class:`AzureBlobStore` is
expected to mirror: idempotent ``put``, ``exists``, ``iter_keys``,
``delete``, and content-hash collision detection.
"""

from __future__ import annotations

import pytest
from horizons_ingestion.blob import MemoryBlobStore


async def test_put_then_exists_then_get_via_iter() -> None:
    store = MemoryBlobStore()
    await store.put("abc.md", b"hello")
    assert await store.exists("abc.md") is True
    keys = [k async for k in store.iter_keys()]
    assert keys == ["abc.md"]


async def test_missing_key_does_not_exist() -> None:
    store = MemoryBlobStore()
    assert await store.exists("nope.md") is False


async def test_put_is_idempotent_with_identical_bytes() -> None:
    store = MemoryBlobStore()
    await store.put("abc.md", b"hello")
    await store.put("abc.md", b"hello")  # no raise; no-op
    assert len(store) == 1


async def test_put_rejects_collision_with_different_bytes() -> None:
    store = MemoryBlobStore()
    await store.put("abc.md", b"hello")
    with pytest.raises(ValueError, match="content-addressed"):
        await store.put("abc.md", b"goodbye")


async def test_delete_is_idempotent_when_key_missing() -> None:
    store = MemoryBlobStore()
    await store.delete("nope.md")  # no raise
    await store.put("abc.md", b"x")
    await store.delete("abc.md")
    assert await store.exists("abc.md") is False


async def test_snapshot_returns_independent_copy() -> None:
    store = MemoryBlobStore()
    await store.put("a.md", b"1")
    snap = store.snapshot()
    await store.put("b.md", b"2")
    assert snap == {"a.md": b"1"}

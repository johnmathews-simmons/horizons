"""Blob-storage Protocol + in-memory implementation.

The Protocol is what ``poll.py`` and ``sweep.py`` depend on. The
in-memory impl is what tests use; the Azure impl lives in ``azure.py``.

Method contract (all impls):

- :meth:`BlobStore.exists` returns ``True`` iff a blob at ``key`` exists.
- :meth:`BlobStore.put` is idempotent: writing the same ``key`` twice
  is a no-op (content-addressing means same key → same bytes).
- :meth:`BlobStore.iter_keys` yields every blob key currently in the
  container, in unspecified order.
- :meth:`BlobStore.delete` removes the blob at ``key``. Missing keys
  are not an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class BlobStore(Protocol):
    """The surface the ingestion worker depends on."""

    async def exists(self, key: str) -> bool: ...

    async def put(self, key: str, body: bytes) -> None: ...

    def iter_keys(self) -> AsyncIterator[str]: ...

    async def delete(self, key: str) -> None: ...


class MemoryBlobStore:
    """In-memory blob store for tests and local fast-iteration.

    No persistence between processes. Idempotent ``put`` matches the
    Protocol: writing the same key with different bytes raises
    ``ValueError`` so tests catch hash collisions immediately (the
    Azure impl would silently overwrite — but since keys are
    content-addressed by sha256, a same-key collision means the body
    bytes are byte-identical too, so no real caller hits this branch).
    """

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def put(self, key: str, body: bytes) -> None:
        existing = self._data.get(key)
        if existing is None:
            self._data[key] = body
            return
        if existing != body:
            raise ValueError(
                f"MemoryBlobStore: refusing to overwrite {key!r} with different bytes "
                "(content-addressed keys should never collide on different content)"
            )

    async def iter_keys(self) -> AsyncIterator[str]:
        for key in list(self._data):
            yield key

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def __len__(self) -> int:
        return len(self._data)

    def snapshot(self) -> dict[str, bytes]:
        """Read-only copy. Useful for test assertions."""
        return dict(self._data)

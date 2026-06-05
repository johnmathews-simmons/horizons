"""Blob-storage abstractions for the ingestion worker.

The worker writes original markdown to content-addressed blobs
(``originals/<sha256>.md``) outside the Postgres transaction. Two
substrates implement the same :class:`BlobStore` Protocol: the
in-memory :class:`MemoryBlobStore` (tests, local fast-iteration) and
the :class:`AzureBlobStore` against Azure Blob Storage (production).

See ``../poll.md`` for the design and ``../sweep.md`` for the orphan
reclaimer.
"""

from horizons_ingestion.blob.azure import AzureBlobStore
from horizons_ingestion.blob.store import BlobStore, MemoryBlobStore

__all__ = [
    "AzureBlobStore",
    "BlobStore",
    "MemoryBlobStore",
]

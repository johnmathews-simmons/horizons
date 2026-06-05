"""Azure Blob Storage implementation of the :class:`BlobStore` Protocol.

Uses ``azure.storage.blob.aio`` (the async surface) with whatever
credential the caller passes — production wires
``azure.identity.aio.DefaultAzureCredential`` so the worker picks up
Workload Identity on ACA / `AZURE_*` env vars / Azure CLI auth in dev
in that order. Tests don't use this impl — they use
:class:`MemoryBlobStore` from ``store.py``.

The Azure SDK objects are heavy (connection pools, credential
caches); the worker constructs the :class:`AzureBlobStore` once at
startup and reuses it for the process lifetime. The class is an
``async`` context manager so the SDK's network resources release
cleanly on shutdown.

See ``../poll.md`` for the operating contract and
``../../../../docs/4. services.md`` §"Ingestion service" for the
two-substrate transaction model.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Self

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob.aio import BlobServiceClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType

    from azure.core.credentials_async import AsyncTokenCredential
    from azure.storage.blob.aio import ContainerClient


class AzureBlobStore:
    """Production :class:`BlobStore` against Azure Blob Storage.

    Construct with the storage account URL
    (e.g. ``https://acct.blob.core.windows.net``), the container name
    (default ``"originals"``), and any
    :class:`azure.core.credentials_async.AsyncTokenCredential`. The
    caller owns the credential's lifecycle.
    """

    def __init__(
        self,
        *,
        account_url: str,
        container: str,
        credential: AsyncTokenCredential,
    ) -> None:
        self._service = BlobServiceClient(
            account_url=account_url,
            credential=credential,
        )
        self._container_name = container
        self._container: ContainerClient | None = None

    async def __aenter__(self) -> Self:
        # Create the container on first use if missing — idempotent and
        # cheap. ResourceExistsError is the no-op signal.
        client = self._service.get_container_client(self._container_name)
        with contextlib.suppress(ResourceExistsError):
            await client.create_container()
        self._container = client
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._container is not None:
            await self._container.close()
        await self._service.close()

    def _client(self) -> ContainerClient:
        if self._container is None:
            raise RuntimeError(
                "AzureBlobStore not entered — use `async with AzureBlobStore(...) as store`"
            )
        return self._container

    async def exists(self, key: str) -> bool:
        blob = self._client().get_blob_client(key)
        return bool(await blob.exists())

    async def put(self, key: str, body: bytes) -> None:
        # ``overwrite=False`` makes ``put`` idempotent for
        # content-addressed keys: a second write of the same sha256
        # raises ResourceExistsError, which we silence. A *different*
        # body under the same key indicates a hash collision and we
        # let it bubble so the caller knows.
        blob = self._client().get_blob_client(key)
        with contextlib.suppress(ResourceExistsError):
            await blob.upload_blob(body, overwrite=False)

    async def iter_keys(self) -> AsyncIterator[str]:
        async for blob in self._client().list_blobs():
            yield blob.name

    async def delete(self, key: str) -> None:
        blob = self._client().get_blob_client(key)
        with contextlib.suppress(ResourceNotFoundError):
            await blob.delete_blob()

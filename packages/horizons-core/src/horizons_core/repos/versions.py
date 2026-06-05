"""``DocumentVersionsRepository`` and its DTO.

Corpus read surface for ``api_app``. The
``document_versions_in_scope`` RLS policy walks the FK up to
``documents`` and joins through ``app_private.current_scope()``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from horizons_core.db.models.versions import DocumentVersion

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class DocumentVersionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    document_id: uuid.UUID
    version_label: str
    publication_date: datetime | None
    effective_date: datetime | None
    content_blob_container: str
    content_blob_key: str
    content_sha256: bytes
    content_bytes: int
    created_at: datetime


class DocumentVersionsRepository:
    dto_type: ClassVar[type[BaseModel]] = DocumentVersionDTO

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_document(self, document_id: uuid.UUID) -> list[DocumentVersionDTO]:
        """Every version of ``document_id`` the current scope can see.

        If the parent document is out of scope the RLS policy filters
        the child rows out too — the join walks up the FK chain. The
        repo therefore returns an empty list rather than raising.
        """
        rows = (
            (
                await self._session.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == document_id)
                    .order_by(DocumentVersion.effective_date)
                )
            )
            .scalars()
            .all()
        )
        return [DocumentVersionDTO.model_validate(r) for r in rows]

    async def get_by_id(self, version_id: uuid.UUID) -> DocumentVersionDTO | None:
        row = (
            await self._session.execute(
                select(DocumentVersion).where(DocumentVersion.id == version_id)
            )
        ).scalar_one_or_none()
        return DocumentVersionDTO.model_validate(row) if row is not None else None

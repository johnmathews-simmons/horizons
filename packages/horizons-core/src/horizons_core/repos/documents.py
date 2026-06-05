"""``DocumentsRepository`` and its DTO.

Corpus read surface for ``api_app``. Rows are filtered by the
``documents_in_scope`` RLS policy, which joins
``app_private.current_scope()`` against ``(jurisdiction, sector)``.
Writes belong to the ingestion worker and land in Track 3 — there are
no write methods here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from horizons_core.db.models.documents import Document

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class DocumentDTO(BaseModel):
    """Serialisable view of a ``documents`` row."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    jurisdiction: str
    sector: str
    lawstronaut_document_id: str
    title: str
    created_at: datetime


class DocumentsRepository:
    dto_type: ClassVar[type[BaseModel]] = DocumentDTO

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[DocumentDTO]:
        """Every document the current session's subscription scope covers.

        The ``documents_in_scope`` RLS policy filters; the repo does not
        re-derive scope.
        """
        rows = (await self._session.execute(select(Document))).scalars().all()
        return [DocumentDTO.model_validate(r) for r in rows]

    async def get_by_id(self, document_id: uuid.UUID) -> DocumentDTO | None:
        """Fetch one document by PK, or ``None`` if RLS filters it out."""
        row = (
            await self._session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        return DocumentDTO.model_validate(row) if row is not None else None

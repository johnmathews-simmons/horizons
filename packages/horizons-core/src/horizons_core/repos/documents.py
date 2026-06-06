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
from sqlalchemy import func, select

from horizons_core.db.models.documents import Document

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_MAX_LIST_LIMIT = 200


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

    async def list_filtered(
        self,
        *,
        jurisdiction: str | None = None,
        sector: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentDTO], int]:
        """Filtered, paginated list of in-scope documents.

        Returns ``(rows, total)`` where ``total`` is the unpaginated
        count under the same filters. The filters are AND-ed; ``search``
        does a case-insensitive substring match on ``title``. RLS still
        applies — out-of-scope rows are absent from both the page and
        the total.
        """
        if limit < 1:
            limit = 1
        if limit > _MAX_LIST_LIMIT:
            limit = _MAX_LIST_LIMIT
        if offset < 0:
            offset = 0

        stmt = select(Document)
        if jurisdiction is not None:
            stmt = stmt.where(Document.jurisdiction == jurisdiction)
        if sector is not None:
            stmt = stmt.where(Document.sector == sector)
        if search:
            stmt = stmt.where(Document.title.ilike(f"%{search}%"))

        total = (
            await self._session.execute(
                stmt.with_only_columns(func.count(Document.id)).order_by(None)
            )
        ).scalar_one()

        page_stmt = (
            stmt.order_by(Document.created_at.desc(), Document.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(page_stmt)).scalars().all()
        return [DocumentDTO.model_validate(r) for r in rows], int(total)

    async def get_by_id(self, document_id: uuid.UUID) -> DocumentDTO | None:
        """Fetch one document by PK, or ``None`` if RLS filters it out."""
        row = (
            await self._session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        return DocumentDTO.model_validate(row) if row is not None else None

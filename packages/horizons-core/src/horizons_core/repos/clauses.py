"""``ClausesRepository`` and its DTO.

Corpus read surface for ``api_app``. The ``clauses_in_scope`` RLS
policy walks the FK chain ``clauses`` → ``document_versions`` →
``documents`` and joins through ``app_private.current_scope()``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from horizons_core.db.models.clauses import Clause

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ClauseDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    document_version_id: uuid.UUID
    clause_uid: uuid.UUID
    clause_path: str
    text_content: str
    ord: int


class ClausesRepository:
    dto_type: ClassVar[type[BaseModel]] = ClauseDTO

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_version(self, version_id: uuid.UUID) -> list[ClauseDTO]:
        """Every clause in ``version_id`` the current scope can see.

        Ordered by ``ord`` to give a stable document-order read. RLS
        propagates from the grand-parent document, so an out-of-scope
        version produces an empty list.
        """
        rows = (
            (
                await self._session.execute(
                    select(Clause)
                    .where(Clause.document_version_id == version_id)
                    .order_by(Clause.ord)
                )
            )
            .scalars()
            .all()
        )
        return [ClauseDTO.model_validate(r) for r in rows]

    async def get_by_id(self, clause_id: uuid.UUID) -> ClauseDTO | None:
        row = (
            await self._session.execute(select(Clause).where(Clause.id == clause_id))
        ).scalar_one_or_none()
        return ClauseDTO.model_validate(row) if row is not None else None

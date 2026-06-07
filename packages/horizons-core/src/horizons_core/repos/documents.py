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
from sqlalchemy import case, func, select
from sqlalchemy.orm import aliased

from horizons_core.db.models.change_events import ChangeEvent
from horizons_core.db.models.clauses import Clause
from horizons_core.db.models.documents import Document
from horizons_core.db.models.versions import DocumentVersion

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


class ChangeCountsDTO(BaseModel):
    """Per-change-type counts attributable to the latest document version."""

    model_config = ConfigDict(frozen=True)

    added: int = 0
    removed: int = 0
    modified: int = 0
    moved: int = 0


class DocumentStatsDTO(BaseModel):
    """``DocumentDTO`` plus per-document aggregates for the table view.

    The four aggregate fields are derived from the **latest** version
    (ranked by ``effective_date desc nulls last, created_at desc``):

    - ``clause_count`` — number of clauses in the latest version.
    - ``change_counts`` — bucketed ``change_events`` for the latest version.
    - ``previous_version_at`` / ``current_version_at`` — sort timestamps
      of the second-latest and latest versions (``None`` when the
      version doesn't exist).
    """

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    jurisdiction: str
    sector: str
    lawstronaut_document_id: str
    title: str
    created_at: datetime
    clause_count: int = 0
    change_counts: ChangeCountsDTO = ChangeCountsDTO()
    previous_version_at: datetime | None = None
    current_version_at: datetime | None = None


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

    async def list_filtered_with_stats(
        self,
        *,
        jurisdiction: str | None = None,
        sector: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentStatsDTO], int]:
        """Filtered, paginated list — each row carries the four aggregate fields.

        Strategy: delegate scope/pagination to ``list_filtered`` (which
        already honours RLS and the same filter shape), then run a
        single window-function query against ``document_versions`` for
        the page's ``id`` set to derive clause counts, per-type change
        counts, and the last-two-version datetimes. One round trip for
        stats per page, bounded by ``limit``.
        """
        rows, total = await self.list_filtered(
            jurisdiction=jurisdiction,
            sector=sector,
            search=search,
            limit=limit,
            offset=offset,
        )
        if not rows:
            return [], total

        doc_ids = [r.id for r in rows]
        stats_by_id = await self._fetch_stats(doc_ids)
        enriched = [self._merge_stats(r, stats_by_id.get(r.id)) for r in rows]
        return enriched, total

    async def get_by_id_with_stats(self, document_id: uuid.UUID) -> DocumentStatsDTO | None:
        """Single document by PK plus the same aggregate fields, or ``None``."""
        base = await self.get_by_id(document_id)
        if base is None:
            return None
        stats_by_id = await self._fetch_stats([document_id])
        return self._merge_stats(base, stats_by_id.get(document_id))

    async def _fetch_stats(self, doc_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, object]]:
        """Single query that returns per-doc aggregates keyed by document id.

        Returns a dict with keys ``clause_count``, ``added``, ``removed``,
        ``modified``, ``moved``, ``previous_version_at``,
        ``current_version_at``. Documents with no versions appear with
        zero counts and ``None`` datetimes. RLS still applies — the
        caller has already filtered via ``list_filtered`` / ``get_by_id``,
        so referenced rows are already in scope.
        """
        sort_at = func.coalesce(DocumentVersion.effective_date, DocumentVersion.created_at).label(
            "sort_at"
        )
        rn = (
            func.row_number()
            .over(
                partition_by=DocumentVersion.document_id,
                order_by=[
                    DocumentVersion.effective_date.desc().nulls_last(),
                    DocumentVersion.created_at.desc(),
                ],
            )
            .label("rn")
        )
        ranked_cte = (
            select(
                DocumentVersion.id.label("id"),
                DocumentVersion.document_id.label("document_id"),
                sort_at,
                rn,
            )
            .where(DocumentVersion.document_id.in_(doc_ids))
            .cte("ranked_versions")
        )

        v_curr = aliased(ranked_cte, name="v_curr")
        v_prev = aliased(ranked_cte, name="v_prev")

        clause_count_subq = (
            select(func.count())
            .select_from(Clause)
            .where(Clause.document_version_id == v_curr.c.id)
            .correlate(v_curr)
            .scalar_subquery()
        )

        stmt = (
            select(
                Document.id.label("document_id"),
                func.coalesce(clause_count_subq, 0).label("clause_count"),
                func.coalesce(
                    func.sum(case((ChangeEvent.change_type == "ADDED", 1), else_=0)), 0
                ).label("added"),
                func.coalesce(
                    func.sum(case((ChangeEvent.change_type == "REMOVED", 1), else_=0)), 0
                ).label("removed"),
                func.coalesce(
                    func.sum(case((ChangeEvent.change_type == "MODIFIED", 1), else_=0)), 0
                ).label("modified"),
                func.coalesce(
                    func.sum(case((ChangeEvent.change_type == "MOVED", 1), else_=0)), 0
                ).label("moved"),
                v_prev.c.sort_at.label("previous_version_at"),
                v_curr.c.sort_at.label("current_version_at"),
            )
            .select_from(Document)
            .outerjoin(
                v_curr,
                (v_curr.c.document_id == Document.id) & (v_curr.c.rn == 1),
            )
            .outerjoin(
                v_prev,
                (v_prev.c.document_id == Document.id) & (v_prev.c.rn == 2),
            )
            .outerjoin(ChangeEvent, ChangeEvent.document_version_id == v_curr.c.id)
            .where(Document.id.in_(doc_ids))
            .group_by(Document.id, v_curr.c.id, v_curr.c.sort_at, v_prev.c.sort_at)
        )

        result = await self._session.execute(stmt)
        out: dict[uuid.UUID, dict[str, object]] = {}
        for row in result.mappings():
            out[row["document_id"]] = {
                "clause_count": int(row["clause_count"] or 0),
                "added": int(row["added"] or 0),
                "removed": int(row["removed"] or 0),
                "modified": int(row["modified"] or 0),
                "moved": int(row["moved"] or 0),
                "previous_version_at": row["previous_version_at"],
                "current_version_at": row["current_version_at"],
            }
        return out

    @staticmethod
    def _merge_stats(base: DocumentDTO, stats: dict[str, object] | None) -> DocumentStatsDTO:
        if stats is None:
            return DocumentStatsDTO(
                id=base.id,
                jurisdiction=base.jurisdiction,
                sector=base.sector,
                lawstronaut_document_id=base.lawstronaut_document_id,
                title=base.title,
                created_at=base.created_at,
            )
        previous_at = stats["previous_version_at"]
        current_at = stats["current_version_at"]
        if previous_at is not None and not isinstance(previous_at, datetime):
            raise TypeError(
                f"Expected previous_version_at to be datetime or None, got {type(previous_at)!r}"
            )
        if current_at is not None and not isinstance(current_at, datetime):
            raise TypeError(
                f"Expected current_version_at to be datetime or None, got {type(current_at)!r}"
            )
        return DocumentStatsDTO(
            id=base.id,
            jurisdiction=base.jurisdiction,
            sector=base.sector,
            lawstronaut_document_id=base.lawstronaut_document_id,
            title=base.title,
            created_at=base.created_at,
            clause_count=int(stats["clause_count"]),  # type: ignore[arg-type]
            change_counts=ChangeCountsDTO(
                added=int(stats["added"]),  # type: ignore[arg-type]
                removed=int(stats["removed"]),  # type: ignore[arg-type]
                modified=int(stats["modified"]),  # type: ignore[arg-type]
                moved=int(stats["moved"]),  # type: ignore[arg-type]
            ),
            previous_version_at=previous_at,
            current_version_at=current_at,
        )

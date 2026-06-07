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
from sqlalchemy import func, select, text

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
        stmt = text(
            """
            WITH ranked_versions AS (
                SELECT
                    v.id,
                    v.document_id,
                    COALESCE(v.effective_date, v.created_at) AS sort_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY v.document_id
                        ORDER BY v.effective_date DESC NULLS LAST,
                                 v.created_at DESC
                    ) AS rn
                FROM document_versions v
                WHERE v.document_id = ANY(CAST(:doc_ids AS uuid[]))
            ),
            v_curr AS (
                SELECT * FROM ranked_versions WHERE rn = 1
            ),
            v_prev AS (
                SELECT * FROM ranked_versions WHERE rn = 2
            ),
            clause_counts AS (
                SELECT c.document_version_id, COUNT(*) AS clause_count
                FROM clauses c
                WHERE c.document_version_id IN (SELECT id FROM v_curr)
                GROUP BY c.document_version_id
            ),
            change_counts AS (
                SELECT
                    ce.document_version_id,
                    SUM(CASE WHEN ce.change_type = 'ADDED'    THEN 1 ELSE 0 END) AS added,
                    SUM(CASE WHEN ce.change_type = 'REMOVED'  THEN 1 ELSE 0 END) AS removed,
                    SUM(CASE WHEN ce.change_type = 'MODIFIED' THEN 1 ELSE 0 END) AS modified,
                    SUM(CASE WHEN ce.change_type = 'MOVED'    THEN 1 ELSE 0 END) AS moved
                FROM change_events ce
                WHERE ce.document_version_id IN (SELECT id FROM v_curr)
                GROUP BY ce.document_version_id
            )
            SELECT
                d.id AS document_id,
                COALESCE(cc.clause_count, 0) AS clause_count,
                COALESCE(ch.added, 0) AS added,
                COALESCE(ch.removed, 0) AS removed,
                COALESCE(ch.modified, 0) AS modified,
                COALESCE(ch.moved, 0) AS moved,
                v_prev.sort_at AS previous_version_at,
                v_curr.sort_at AS current_version_at
            FROM unnest(CAST(:doc_ids AS uuid[])) AS d(id)
            LEFT JOIN v_curr        ON v_curr.document_id = d.id
            LEFT JOIN v_prev        ON v_prev.document_id = d.id
            LEFT JOIN clause_counts cc ON cc.document_version_id = v_curr.id
            LEFT JOIN change_counts ch ON ch.document_version_id = v_curr.id
            """
        )

        result = await self._session.execute(stmt, {"doc_ids": doc_ids})
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
        assert previous_at is None or isinstance(previous_at, datetime)
        assert current_at is None or isinstance(current_at, datetime)
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

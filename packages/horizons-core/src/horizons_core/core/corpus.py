"""``corpus_shape(session)`` — the corpus-wide ``(jurisdiction, sector)`` matrix.

Reads through the ``app_public.corpus_shape()`` SECURITY DEFINER
function (migration 0013). Returns *every* pair present in
``documents``, regardless of the caller's subscription scope. Used by
``/v1/me/overview`` to render "not subscribed" cards on the home
dashboard.

Why SECURITY DEFINER: corpus shape is non-sensitive catalog data
(clients already know the subscription token vocabulary), and reading
it via ``admin_bypass`` per request would force a per-page-load audit
row. The function is owned by the database role that can read
``documents`` unscoped; ``api_app`` and ``admin_bypass`` are granted
``EXECUTE``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, String, select
from sqlalchemy.dialects.postgresql import BIGINT
from sqlalchemy.sql import func

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class CorpusShapeRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    jurisdiction: str
    sector: str
    document_count: int


class ChangeEventShapeRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    jurisdiction: str
    sector: str
    change_count: int


async def corpus_shape(session: AsyncSession) -> list[CorpusShapeRow]:
    cs = (
        func.app_public.corpus_shape()
        .table_valued(
            Column("jurisdiction", String),
            Column("sector", String),
            Column("document_count", BIGINT),
        )
        .alias("cs")
    )
    rows = (
        await session.execute(select(cs.c.jurisdiction, cs.c.sector, cs.c.document_count))
    ).all()
    return [
        CorpusShapeRow(
            jurisdiction=r.jurisdiction,
            sector=r.sector,
            document_count=int(r.document_count),
        )
        for r in rows
    ]


async def change_event_shape(session: AsyncSession) -> list[ChangeEventShapeRow]:
    """Per-(jurisdiction, sector) count of recorded change events.

    Reads through the ``app_public.change_event_shape()`` SECURITY
    DEFINER function (migration 0014). Same rationale as
    ``corpus_shape``: change-event counts are catalog-shape, not tenant
    data, so we expose the unscoped roll-up via SECURITY DEFINER rather
    than escalating to admin_bypass per page load.
    """
    ces = (
        func.app_public.change_event_shape()
        .table_valued(
            Column("jurisdiction", String),
            Column("sector", String),
            Column("change_count", BIGINT),
        )
        .alias("ces")
    )
    rows = (
        await session.execute(select(ces.c.jurisdiction, ces.c.sector, ces.c.change_count))
    ).all()
    return [
        ChangeEventShapeRow(
            jurisdiction=r.jurisdiction,
            sector=r.sector,
            change_count=int(r.change_count),
        )
        for r in rows
    ]

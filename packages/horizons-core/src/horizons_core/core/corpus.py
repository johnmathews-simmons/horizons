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

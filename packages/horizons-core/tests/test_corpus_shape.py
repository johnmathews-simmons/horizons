"""Integration test for ``corpus_shape`` — runs against a real Postgres.

Fixture naming note: the plan spec used ``pg_session_admin`` /
``pg_session_api_app`` as placeholder names. No such fixtures exist in
this package; the actual fixtures (defined in
``packages/horizons-core/tests/conftest.py``) are:

- ``admin_session`` — ``AsyncSession`` under ``admin_bypass`` role (BYPASSRLS,
  SELECT-only on corpus tables).
- ``api_app_session`` — ``AsyncSession`` under ``api_app`` role with a
  throwaway ``app.user_id`` GUC bound.
- ``migrated_engine`` — sync superuser ``Engine`` used for seed inserts,
  because ``admin_bypass`` has only SELECT on corpus tables; INSERT
  requires the superuser or ``ingestion_worker`` role.

These follow the ``TwoClients`` pattern in ``tests/isolation/conftest.py``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from horizons_core.core.corpus import CorpusShapeRow, corpus_shape
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _insert_document(engine: Engine, *, jurisdiction: str, sector: str) -> None:
    """Insert a document row as the superuser (bypasses role restrictions)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO documents "
                "(jurisdiction, sector, lawstronaut_document_id, title) "
                "VALUES (:j, :s, :lid, :t)"
            ),
            {
                "j": jurisdiction,
                "s": sector,
                "lid": f"test-corpus-shape-{uuid.uuid4()}",
                "t": f"{jurisdiction}/{sector}",
            },
        )


async def test_corpus_shape_returns_grouped_counts(
    migrated_engine: Engine, admin_session: AsyncSession
) -> None:
    _insert_document(migrated_engine, jurisdiction="UK", sector="BANKING")
    _insert_document(migrated_engine, jurisdiction="UK", sector="BANKING")
    _insert_document(migrated_engine, jurisdiction="EU", sector="BANKING")

    rows = await corpus_shape(admin_session)

    by_pair = {(r.jurisdiction, r.sector): r.document_count for r in rows}
    assert by_pair[("UK", "BANKING")] >= 2
    assert by_pair[("EU", "BANKING")] >= 1


async def test_corpus_shape_visible_under_api_app(
    migrated_engine: Engine,
    api_app_session: AsyncSession,
) -> None:
    _insert_document(migrated_engine, jurisdiction="UK", sector="BANKING")

    rows = await corpus_shape(api_app_session)

    assert any(
        r.jurisdiction == "UK" and r.sector == "BANKING" and r.document_count >= 1 for r in rows
    )


async def test_corpus_shape_dto_types(migrated_engine: Engine, admin_session: AsyncSession) -> None:
    _insert_document(migrated_engine, jurisdiction="IE", sector="BANKING")

    rows = await corpus_shape(admin_session)

    assert rows
    row = rows[0]
    assert isinstance(row, CorpusShapeRow)
    assert isinstance(row.jurisdiction, str)
    assert isinstance(row.sector, str)
    assert isinstance(row.document_count, int)

"""Tests for ``DocumentsRepository.list_filtered_with_stats`` /
``get_by_id_with_stats`` — the clause count, per-type change counts, and
last-two-version datetimes that drive the new documents table view.

Inserts go through ``migrated_engine`` (sync, superuser) because the
``admin_bypass`` role attached to ``admin_session`` is SELECT-only on
corpus tables; reads go through the repo on ``admin_session``.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy
from horizons_core.repos.documents import DocumentsRepository

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _sha256() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _insert_document(conn: Connection, *, jurisdiction: str, sector: str, title: str) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            """
            INSERT INTO documents (jurisdiction, sector, lawstronaut_document_id, title)
            VALUES (:j, :s, :ldid, :t)
            RETURNING id
            """
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "ldid": f"ldid-{uuid.uuid4().hex[:12]}",
            "t": title,
        },
    ).scalar_one()


def _insert_version(
    conn: Connection,
    *,
    document_id: uuid.UUID,
    label: str,
    effective_date: datetime | None,
    clause_count: int,
) -> uuid.UUID:
    version_id = conn.execute(
        sqlalchemy.text(
            """
            INSERT INTO document_versions (
                document_id, version_label, effective_date,
                content_blob_container, content_blob_key, content_sha256, content_bytes
            )
            VALUES (:did, :lbl, :eff, 'ce', :k, :h, 1024)
            RETURNING id
            """
        ),
        {
            "did": document_id,
            "lbl": label,
            "eff": effective_date,
            "k": f"k-{uuid.uuid4().hex}",
            "h": _sha256(),
        },
    ).scalar_one()
    for ord_ in range(clause_count):
        conn.execute(
            sqlalchemy.text(
                """
                INSERT INTO clauses (
                    document_version_id, clause_uid, clause_path, text_content, ord
                )
                VALUES (:vid, :uid, :path, 'body', :ord)
                """
            ),
            {
                "vid": version_id,
                "uid": uuid.uuid4(),
                "path": f"/{ord_}",
                "ord": ord_,
            },
        )
    return version_id


def _insert_change_event(
    conn: Connection,
    *,
    document_id: uuid.UUID,
    document_version_id: uuid.UUID,
    jurisdiction: str,
    sector: str,
    change_type: str,
) -> None:
    conn.execute(
        sqlalchemy.text(
            """
            INSERT INTO change_events
                (document_id, document_version_id, jurisdiction, sector,
                 change_type, alignment_confidence, detected_at)
            VALUES (:did, :vid, :j, :s, :ct, 0.99, NOW())
            """
        ),
        {
            "did": document_id,
            "vid": document_version_id,
            "j": jurisdiction,
            "s": sector,
            "ct": change_type,
        },
    )


def _rand_jurisdiction(prefix: str) -> str:
    # Unique per-test jurisdiction so cross-test seeds don't bleed into
    # ``list_filtered_with_stats(jurisdiction=...)`` assertions.
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def test_two_version_document_returns_counts_and_datetimes(
    migrated_engine: Engine, admin_session: AsyncSession
) -> None:
    jurisdiction = _rand_jurisdiction("UK")
    v1_eff = datetime(2025, 1, 1, tzinfo=UTC)
    v2_eff = datetime(2026, 1, 1, tzinfo=UTC)
    with migrated_engine.begin() as conn:
        doc_id = _insert_document(
            conn, jurisdiction=jurisdiction, sector="banking", title="Test Act"
        )
        _ = _insert_version(
            conn, document_id=doc_id, label="v1", effective_date=v1_eff, clause_count=10
        )
        v2 = _insert_version(
            conn, document_id=doc_id, label="v2", effective_date=v2_eff, clause_count=12
        )
        for ct in ("ADDED", "ADDED", "REMOVED", "MODIFIED", "MODIFIED", "MODIFIED", "MOVED"):
            _insert_change_event(
                conn,
                document_id=doc_id,
                document_version_id=v2,
                jurisdiction=jurisdiction,
                sector="banking",
                change_type=ct,
            )

    rows, total = await DocumentsRepository(admin_session).list_filtered_with_stats(
        jurisdiction=jurisdiction
    )

    assert total == 1
    assert len(rows) == 1
    row = rows[0]
    assert row.id == doc_id
    assert row.clause_count == 12
    assert row.change_counts.added == 2
    assert row.change_counts.removed == 1
    assert row.change_counts.modified == 3
    assert row.change_counts.moved == 1
    assert row.previous_version_at == v1_eff
    assert row.current_version_at == v2_eff


async def test_one_version_document_has_zero_counts_and_null_previous(
    migrated_engine: Engine, admin_session: AsyncSession
) -> None:
    jurisdiction = _rand_jurisdiction("UK")
    v_eff = datetime(2026, 1, 1, tzinfo=UTC)
    with migrated_engine.begin() as conn:
        doc_id = _insert_document(
            conn, jurisdiction=jurisdiction, sector="banking", title="Sole-version Act"
        )
        _insert_version(conn, document_id=doc_id, label="v1", effective_date=v_eff, clause_count=8)

    rows, _total = await DocumentsRepository(admin_session).list_filtered_with_stats(
        jurisdiction=jurisdiction
    )

    row = next(r for r in rows if r.id == doc_id)
    assert row.clause_count == 8
    assert row.change_counts.added == 0
    assert row.change_counts.removed == 0
    assert row.change_counts.modified == 0
    assert row.change_counts.moved == 0
    assert row.previous_version_at is None
    assert row.current_version_at == v_eff


async def test_zero_version_document_has_null_datetimes(
    migrated_engine: Engine, admin_session: AsyncSession
) -> None:
    jurisdiction = _rand_jurisdiction("UK")
    with migrated_engine.begin() as conn:
        doc_id = _insert_document(
            conn, jurisdiction=jurisdiction, sector="banking", title="Empty Act"
        )

    rows, _total = await DocumentsRepository(admin_session).list_filtered_with_stats(
        jurisdiction=jurisdiction
    )

    row = next(r for r in rows if r.id == doc_id)
    assert row.clause_count == 0
    assert row.change_counts.added == 0
    assert row.previous_version_at is None
    assert row.current_version_at is None


async def test_get_by_id_with_stats_returns_same_shape(
    migrated_engine: Engine, admin_session: AsyncSession
) -> None:
    jurisdiction = _rand_jurisdiction("UK")
    v1_eff = datetime(2025, 1, 1, tzinfo=UTC)
    v2_eff = datetime(2026, 1, 1, tzinfo=UTC)
    with migrated_engine.begin() as conn:
        doc_id = _insert_document(
            conn, jurisdiction=jurisdiction, sector="banking", title="Detail Act"
        )
        _ = _insert_version(
            conn, document_id=doc_id, label="v1", effective_date=v1_eff, clause_count=4
        )
        v2 = _insert_version(
            conn, document_id=doc_id, label="v2", effective_date=v2_eff, clause_count=5
        )
        _insert_change_event(
            conn,
            document_id=doc_id,
            document_version_id=v2,
            jurisdiction=jurisdiction,
            sector="banking",
            change_type="ADDED",
        )

    row = await DocumentsRepository(admin_session).get_by_id_with_stats(doc_id)

    assert row is not None
    assert row.id == doc_id
    assert row.clause_count == 5
    assert row.change_counts.added == 1
    assert row.previous_version_at == v1_eff
    assert row.current_version_at == v2_eff


async def test_synthetic_v2_with_null_effective_date_is_ranked_current(
    migrated_engine: Engine, admin_session: AsyncSession
) -> None:
    """Regression: a NULL-effective_date v2 inserted after a v1 with NO
    effective_date either must rank as the current version, so its
    change_events still join. Mirrors the ``stage_synthetic_v2`` shape
    (both versions have ``effective_date = NULL``; v2 is the later
    insert). Before the COALESCE-based ranking, ``effective_date DESC
    NULLS LAST`` left the choice up to a tiebreaker on ``created_at``,
    which is the transaction-start timestamp in Postgres — so two
    inserts in the same transaction tied, ROW_NUMBER fell back to an
    arbitrary order, and v1 sometimes won. The fix is to rank by
    COALESCE(effective_date, created_at) DESC with ``id DESC`` (uuidv7)
    as a strictly-monotonic tiebreaker.
    """
    jurisdiction = _rand_jurisdiction("IE")
    with migrated_engine.begin() as conn:
        doc_id = _insert_document(
            conn, jurisdiction=jurisdiction, sector="corporate-governance", title="Sync v2 Act"
        )
        # Same transaction → same ``created_at`` for both rows. v2 is
        # only distinguishable from v1 by its uuidv7 id tiebreaker.
        _ = _insert_version(
            conn, document_id=doc_id, label="v1", effective_date=None, clause_count=10
        )
        v2 = _insert_version(
            conn, document_id=doc_id, label="v2-synthetic", effective_date=None, clause_count=12
        )
        for ct in ("ADDED", "ADDED", "REMOVED", "MODIFIED", "MOVED"):
            _insert_change_event(
                conn,
                document_id=doc_id,
                document_version_id=v2,
                jurisdiction=jurisdiction,
                sector="corporate-governance",
                change_type=ct,
            )

    rows, _total = await DocumentsRepository(admin_session).list_filtered_with_stats(
        jurisdiction=jurisdiction
    )

    row = next(r for r in rows if r.id == doc_id)
    assert row.clause_count == 12, "clause_count should reflect v2 (12), not v1 (10)"
    assert row.change_counts.added == 2
    assert row.change_counts.removed == 1
    assert row.change_counts.modified == 1
    assert row.change_counts.moved == 1
    # Both versions share an effective_date of NULL and a transaction
    # ``created_at``; the columns simply carry that timestamp through
    # the COALESCE.
    assert row.previous_version_at is not None
    assert row.current_version_at is not None
    assert row.previous_version_at == row.current_version_at


async def test_v1_with_real_effective_date_does_not_outrank_later_null_v2(
    migrated_engine: Engine, admin_session: AsyncSession
) -> None:
    """Regression: v1 with a real effective_date (e.g. publication date
    from 2018) must NOT beat a later-ingested NULL-effective_date v2.
    Plain ``DESC NULLS LAST`` ranked the real-dated v1 first; the fix
    treats NULL effective_date as the ingest time, so v2 still wins.
    """
    jurisdiction = _rand_jurisdiction("UK")
    v1_eff = datetime(2018, 12, 1, tzinfo=UTC)
    with migrated_engine.begin() as conn:
        doc_id = _insert_document(
            conn, jurisdiction=jurisdiction, sector="banking", title="Mixed-effdate Act"
        )
        _ = _insert_version(
            conn, document_id=doc_id, label="v1", effective_date=v1_eff, clause_count=3
        )
        v2 = _insert_version(
            conn, document_id=doc_id, label="v2-synthetic", effective_date=None, clause_count=5
        )
        _insert_change_event(
            conn,
            document_id=doc_id,
            document_version_id=v2,
            jurisdiction=jurisdiction,
            sector="banking",
            change_type="ADDED",
        )

    rows, _total = await DocumentsRepository(admin_session).list_filtered_with_stats(
        jurisdiction=jurisdiction
    )

    row = next(r for r in rows if r.id == doc_id)
    assert row.clause_count == 5, "v2 (the later ingest) should be current"
    assert row.change_counts.added == 1
    # v_prev resolves to v1, so previous_version_at == v1's effective_date.
    assert row.previous_version_at == v1_eff

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
    effective_date: datetime,
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

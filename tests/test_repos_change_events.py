"""Integration tests for ``ChangeEventsRepository`` (WU4.4).

Coverage:

- ``list_discovery`` at corpus scope returns rows in
  ``(detected_at DESC, id DESC)`` order, filtered by RLS to
  in-scope ``(jurisdiction, sector)`` only.
- Corpus-scope filters (``jurisdiction``, ``sector``, ``since``,
  ``until``) narrow the result set in addition to RLS.
- Document scope returns only events for the given document_id.
- Clause scope returns events touching the given clause_uid on
  either before or after side.
- Opaque cursors page deterministically; ``next_cursor`` is omitted
  on the last page.
- Cursor encode/decode round-trip + bad-cursor handling.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy
from alembic import command
from alembic.config import Config
from horizons_core.db.session import make_engine, session_for_user
from horizons_core.repos.change_events import (
    ChangeEventsRepository,
    ClauseScope,
    CorpusScope,
    CursorError,
    DocumentScope,
    decode_cursor,
    encode_cursor,
)
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy import Connection, Engine
    from sqlalchemy.ext.asyncio import AsyncEngine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


@pytest.fixture
def migrated_db(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, str]]:
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    async_url = postgres_container.get_connection_url(driver="asyncpg")
    monkeypatch.setenv("HORIZONS_DB_URL", sync_url)
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    sync_engine = create_engine(sync_url, future=True)
    try:
        yield sync_engine, async_url
    finally:
        sync_engine.dispose()


@pytest_asyncio.fixture
async def async_engine(
    migrated_db: tuple[Engine, str],
) -> AsyncIterator[AsyncEngine]:
    _, async_url = migrated_db
    eng = make_engine(async_url)
    try:
        yield eng
    finally:
        await eng.dispose()


# ---- seed helpers (sync, superuser, bypasses RLS) -------------------------


def _sha256() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _make_user(conn: Connection, email: str) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO users (email, password_hash, role) "
            "VALUES (:e, 'hash', 'client') RETURNING id"
        ),
        {"e": email},
    ).scalar_one()


def _subscribe(
    conn: Connection,
    user_id: uuid.UUID,
    scopes: list[tuple[str, str]],
) -> None:
    now = datetime.now(UTC)
    sid = conn.execute(
        sqlalchemy.text(
            "INSERT INTO subscriptions (user_id, valid_from, valid_to) "
            "VALUES (:u, :f, NULL) RETURNING id"
        ),
        {"u": user_id, "f": now - timedelta(days=30)},
    ).scalar_one()
    for j, s in scopes:
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO subscription_scopes "
                "(subscription_id, jurisdiction, sector) "
                "VALUES (:s, :j, :sec)"
            ),
            {"s": sid, "j": j, "sec": s},
        )


def _make_doc(
    conn: Connection,
    jurisdiction: str,
    sector: str,
    label: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    doc_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "lid": f"ce_{label}_{uuid.uuid4()}",
            "t": f"ce_{label}",
        },
    ).scalar_one()
    ver_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO document_versions "
            "(document_id, version_label, publication_date, effective_date, "
            "content_blob_container, content_blob_key, content_sha256, "
            "content_bytes) "
            "VALUES (:d, 'v1', :p, :e, 'ce', :k, :h, 100) RETURNING id"
        ),
        {
            "d": doc_id,
            "p": datetime.now(UTC),
            "e": datetime.now(UTC),
            "k": f"{label}/v1.md",
            "h": _sha256(),
        },
    ).scalar_one()
    return doc_id, ver_id


def _insert_event(
    conn: Connection,
    *,
    document_id: uuid.UUID,
    document_version_id: uuid.UUID,
    jurisdiction: str,
    sector: str,
    change_type: str = "MODIFIED",
    detected_at: datetime | None = None,
    effective_date: datetime | None = None,
    before_clause_uid: uuid.UUID | None = None,
    after_clause_uid: uuid.UUID | None = None,
    before_path: str | None = "Part 1 / Section 1",
    after_path: str | None = "Part 1 / Section 1",
    before_text: str | None = "before",
    after_text: str | None = "after",
    alignment_confidence: float = 0.9,
) -> int:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO change_events ("
            "  document_id, document_version_id, jurisdiction, sector, "
            "  change_type, before_clause_uid, after_clause_uid, "
            "  before_path, after_path, before_text, after_text, "
            "  alignment_confidence, detected_at, effective_date"
            ") VALUES ("
            "  :doc, :ver, :j, :sec, :ct, :bcu, :acu, :bp, :ap, :bt, :at, "
            "  :ac, :dt, :ed"
            ") RETURNING id"
        ),
        {
            "doc": document_id,
            "ver": document_version_id,
            "j": jurisdiction,
            "sec": sector,
            "ct": change_type,
            "bcu": before_clause_uid,
            "acu": after_clause_uid,
            "bp": before_path,
            "ap": after_path,
            "bt": before_text,
            "at": after_text,
            "ac": alignment_confidence,
            "dt": detected_at or datetime.now(UTC),
            "ed": effective_date,
        },
    ).scalar_one()


# ---- cursor unit tests ----------------------------------------------------


def test_cursor_round_trip_preserves_position() -> None:
    dt = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)
    token = encode_cursor(dt, 4827)
    assert decode_cursor(token) == (dt, 4827)


def test_decode_cursor_rejects_garbage() -> None:
    with pytest.raises(CursorError):
        decode_cursor("not-base64-!!!")


def test_decode_cursor_rejects_well_formed_but_wrong_shape() -> None:
    import base64 as _b64

    payload = _b64.urlsafe_b64encode(b'{"wrong":"shape"}').rstrip(b"=").decode("ascii")
    with pytest.raises(CursorError):
        decode_cursor(payload)


# ---- corpus scope ---------------------------------------------------------


@pytest.mark.integration
async def test_list_discovery_corpus_returns_in_scope_only(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "ce_corpus_inscope@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        in_doc, in_ver = _make_doc(conn, "UK", "BANKING", "in")
        out_doc, out_ver = _make_doc(conn, "EU", "INSURANCE", "out")
        in_id = _insert_event(
            conn,
            document_id=in_doc,
            document_version_id=in_ver,
            jurisdiction="UK",
            sector="BANKING",
        )
        _insert_event(
            conn,
            document_id=out_doc,
            document_version_id=out_ver,
            jurisdiction="EU",
            sector="INSURANCE",
        )

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        items, next_cursor = await ChangeEventsRepository(session).list_discovery(CorpusScope())

    ids = [it.id for it in items]
    assert in_id in ids
    assert all(it.jurisdiction == "UK" and it.sector == "BANKING" for it in items)
    assert next_cursor is None


@pytest.mark.integration
async def test_list_discovery_corpus_filters_by_jurisdiction_and_sector(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "ce_corpus_filters@example.com")
        _subscribe(conn, uid, [("UK", "BANKING"), ("UK", "INSURANCE")])
        b_doc, b_ver = _make_doc(conn, "UK", "BANKING", "b")
        i_doc, i_ver = _make_doc(conn, "UK", "INSURANCE", "i")
        _insert_event(
            conn,
            document_id=b_doc,
            document_version_id=b_ver,
            jurisdiction="UK",
            sector="BANKING",
        )
        _insert_event(
            conn,
            document_id=i_doc,
            document_version_id=i_ver,
            jurisdiction="UK",
            sector="INSURANCE",
        )

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        items, _ = await ChangeEventsRepository(session).list_discovery(
            CorpusScope(jurisdiction="UK", sector="BANKING")
        )

    assert {it.sector for it in items} == {"BANKING"}


@pytest.mark.integration
async def test_list_discovery_corpus_filters_by_time_window(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    boundary = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    with sync.begin() as conn:
        uid = _make_user(conn, "ce_corpus_time@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        doc, ver = _make_doc(conn, "UK", "BANKING", "t")
        old_id = _insert_event(
            conn,
            document_id=doc,
            document_version_id=ver,
            jurisdiction="UK",
            sector="BANKING",
            detected_at=boundary - timedelta(days=1),
        )
        new_id = _insert_event(
            conn,
            document_id=doc,
            document_version_id=ver,
            jurisdiction="UK",
            sector="BANKING",
            detected_at=boundary + timedelta(days=1),
        )

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        items, _ = await ChangeEventsRepository(session).list_discovery(CorpusScope(since=boundary))

    ids = {it.id for it in items}
    assert new_id in ids
    assert old_id not in ids


@pytest.mark.integration
async def test_list_discovery_ordered_detected_at_desc_then_id_desc(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    # Unique scope per test — the session-scoped testcontainer leaves
    # events behind from earlier tests in the same Postgres lifetime.
    j, s = f"ORD-{uuid.uuid4().hex[:8]}", f"ORD-{uuid.uuid4().hex[:8]}"
    sync, _ = migrated_db
    pin = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)
    with sync.begin() as conn:
        uid = _make_user(conn, "ce_corpus_order@example.com")
        _subscribe(conn, uid, [(j, s)])
        doc, ver = _make_doc(conn, j, s, "o")
        old_id = _insert_event(
            conn,
            document_id=doc,
            document_version_id=ver,
            jurisdiction=j,
            sector=s,
            detected_at=pin - timedelta(hours=1),
        )
        tied_first = _insert_event(
            conn,
            document_id=doc,
            document_version_id=ver,
            jurisdiction=j,
            sector=s,
            detected_at=pin,
        )
        tied_second = _insert_event(
            conn,
            document_id=doc,
            document_version_id=ver,
            jurisdiction=j,
            sector=s,
            detected_at=pin,
        )

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        items, _ = await ChangeEventsRepository(session).list_discovery(CorpusScope())

    assert [it.id for it in items] == [tied_second, tied_first, old_id]


@pytest.mark.integration
async def test_list_discovery_paginates_with_cursor(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    # Unique scope so we walk only this test's 5 rows.
    j, s = f"PAG-{uuid.uuid4().hex[:8]}", f"PAG-{uuid.uuid4().hex[:8]}"
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "ce_corpus_page@example.com")
        _subscribe(conn, uid, [(j, s)])
        doc, ver = _make_doc(conn, j, s, "p")
        inserted_ids = [
            _insert_event(
                conn,
                document_id=doc,
                document_version_id=ver,
                jurisdiction=j,
                sector=s,
                detected_at=datetime(2026, 6, 1, tzinfo=UTC) + timedelta(seconds=i),
            )
            for i in range(5)
        ]

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = ChangeEventsRepository(session)
        page1, cursor1 = await repo.list_discovery(CorpusScope(), limit=2)
        assert cursor1 is not None
        page2, cursor2 = await repo.list_discovery(CorpusScope(), limit=2, cursor=cursor1)
        assert cursor2 is not None
        page3, cursor3 = await repo.list_discovery(CorpusScope(), limit=2, cursor=cursor2)

    walked = [it.id for it in page1 + page2 + page3]
    assert walked == list(reversed(inserted_ids))
    assert cursor3 is None


# ---- document scope -------------------------------------------------------


@pytest.mark.integration
async def test_list_discovery_document_scope_narrows_to_one_document(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "ce_doc_scope@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        target_doc, target_ver = _make_doc(conn, "UK", "BANKING", "target")
        other_doc, other_ver = _make_doc(conn, "UK", "BANKING", "other")
        target_id = _insert_event(
            conn,
            document_id=target_doc,
            document_version_id=target_ver,
            jurisdiction="UK",
            sector="BANKING",
        )
        _insert_event(
            conn,
            document_id=other_doc,
            document_version_id=other_ver,
            jurisdiction="UK",
            sector="BANKING",
        )

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        items, _ = await ChangeEventsRepository(session).list_discovery(
            DocumentScope(document_id=target_doc)
        )

    assert [it.id for it in items] == [target_id]


@pytest.mark.integration
async def test_list_discovery_document_scope_out_of_scope_returns_empty(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    """A foreign document_id returns [] under RLS, not 404 — invisible."""
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "ce_doc_oos@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        foreign_doc, foreign_ver = _make_doc(conn, "EU", "INSURANCE", "foreign")
        _insert_event(
            conn,
            document_id=foreign_doc,
            document_version_id=foreign_ver,
            jurisdiction="EU",
            sector="INSURANCE",
        )

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        items, cursor = await ChangeEventsRepository(session).list_discovery(
            DocumentScope(document_id=foreign_doc)
        )

    assert items == []
    assert cursor is None


# ---- clause scope ---------------------------------------------------------


@pytest.mark.integration
async def test_list_discovery_clause_scope_matches_before_or_after(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    target_uid = uuid.uuid4()
    other_uid = uuid.uuid4()
    with sync.begin() as conn:
        uid = _make_user(conn, "ce_clause_scope@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        doc, ver = _make_doc(conn, "UK", "BANKING", "cls")
        # MODIFIED — uid appears on both sides
        modified_id = _insert_event(
            conn,
            document_id=doc,
            document_version_id=ver,
            jurisdiction="UK",
            sector="BANKING",
            before_clause_uid=target_uid,
            after_clause_uid=target_uid,
            change_type="MODIFIED",
        )
        # ADDED — uid only on the after side
        added_id = _insert_event(
            conn,
            document_id=doc,
            document_version_id=ver,
            jurisdiction="UK",
            sector="BANKING",
            before_clause_uid=None,
            after_clause_uid=target_uid,
            change_type="ADDED",
            before_text=None,
        )
        # REMOVED — uid only on the before side
        removed_id = _insert_event(
            conn,
            document_id=doc,
            document_version_id=ver,
            jurisdiction="UK",
            sector="BANKING",
            before_clause_uid=target_uid,
            after_clause_uid=None,
            change_type="REMOVED",
            after_text=None,
        )
        # Unrelated uid — excluded
        _insert_event(
            conn,
            document_id=doc,
            document_version_id=ver,
            jurisdiction="UK",
            sector="BANKING",
            before_clause_uid=other_uid,
            after_clause_uid=other_uid,
        )

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        items, _ = await ChangeEventsRepository(session).list_discovery(
            ClauseScope(clause_uid=target_uid)
        )

    assert {it.id for it in items} == {modified_id, added_id, removed_id}

"""Integration tests for the corpus repos (WU1.6).

``DocumentsRepository``, ``DocumentVersionsRepository``,
``ClausesRepository`` are exercised under the WU1.5 session bracket
plus ``SET LOCAL ROLE api_app`` so the ``*_in_scope`` policies are the
filter.

Coverage:

- A single in-scope user sees in-scope rows through every repo;
  out-of-scope rows are filtered.
- ``list_for_document`` / ``list_for_version`` return the parent's
  children in DTO form, ordered as the repo promises (effective_date
  for versions, ord for clauses).
- ``get_by_id`` returns a DTO for in-scope rows and ``None`` for rows
  RLS filtered out.

The cross-client gate (B cannot see A's scope) lives in WU1.7.
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
from horizons_core.repos.clauses import ClauseDTO, ClausesRepository
from horizons_core.repos.documents import DocumentDTO, DocumentsRepository
from horizons_core.repos.versions import (
    DocumentVersionDTO,
    DocumentVersionsRepository,
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


# ---- seeding helpers (sync, superuser, bypasses RLS) ---------------------


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
) -> uuid.UUID:
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
    return sid


def _insert_document(
    conn: Connection,
    jurisdiction: str,
    sector: str,
    title: str,
) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "lid": f"repo_corpus_{uuid.uuid4()}",
            "t": title,
        },
    ).scalar_one()


def _insert_version(
    conn: Connection,
    doc_id: uuid.UUID,
    label: str = "v1",
    effective_date: datetime | None = None,
) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO document_versions "
            "(document_id, version_label, publication_date, effective_date, "
            "content_blob_container, content_blob_key, content_sha256, "
            "content_bytes) "
            "VALUES (:d, :l, :p, :e, :c, :k, :h, :b) RETURNING id"
        ),
        {
            "d": doc_id,
            "l": label,
            "p": datetime.now(UTC),
            "e": effective_date or datetime.now(UTC),
            "c": "repo_corpus",
            "k": f"{doc_id}/{label}.md",
            "h": _sha256(),
            "b": 1000,
        },
    ).scalar_one()


def _insert_clause(
    conn: Connection,
    version_id: uuid.UUID,
    path: str,
    ord_value: int,
) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO clauses "
            "(document_version_id, clause_uid, clause_path, text_content, ord) "
            "VALUES (:v, :u, :p, :t, :o) RETURNING id"
        ),
        {
            "v": version_id,
            "u": uuid.uuid4(),
            "p": path,
            "t": f"repo_corpus clause {path}",
            "o": ord_value,
        },
    ).scalar_one()


# ---- tests ---------------------------------------------------------------


@pytest.mark.integration
async def test_documents_list_all_returns_in_scope_dtos(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "repo_corpus_docs@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        in_id = _insert_document(conn, "UK", "BANKING", "repo_docs_in")
        _insert_document(conn, "EU", "INSURANCE", "repo_docs_out")

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = DocumentsRepository(session)
        docs = await repo.list_all()

    by_title = {d.title: d for d in docs}
    assert "repo_docs_in" in by_title
    assert "repo_docs_out" not in by_title
    assert isinstance(by_title["repo_docs_in"], DocumentDTO)
    assert by_title["repo_docs_in"].id == in_id


@pytest.mark.integration
async def test_documents_get_by_id_returns_dto_or_none(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "repo_corpus_get@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        in_id = _insert_document(conn, "UK", "BANKING", "repo_docs_get_in")
        out_id = _insert_document(conn, "EU", "INSURANCE", "repo_docs_get_out")

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = DocumentsRepository(session)
        in_dto = await repo.get_by_id(in_id)
        out_dto = await repo.get_by_id(out_id)
        missing = await repo.get_by_id(uuid.uuid4())

    assert in_dto is not None and in_dto.title == "repo_docs_get_in"
    assert out_dto is None  # RLS filters it
    assert missing is None


@pytest.mark.integration
async def test_versions_list_for_document_ordered_by_effective_date(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "repo_corpus_ver_list@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        doc = _insert_document(conn, "UK", "BANKING", "repo_ver_doc")
        later = _insert_version(conn, doc, "v2", datetime(2026, 1, 1, tzinfo=UTC))
        earlier = _insert_version(conn, doc, "v1", datetime(2025, 1, 1, tzinfo=UTC))

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = DocumentVersionsRepository(session)
        versions = await repo.list_for_document(doc)

    assert [v.id for v in versions] == [earlier, later]
    assert all(isinstance(v, DocumentVersionDTO) for v in versions)


@pytest.mark.integration
async def test_versions_get_by_id_returns_dto_or_none(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "repo_corpus_ver_get@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        in_doc = _insert_document(conn, "UK", "BANKING", "repo_ver_get_in")
        out_doc = _insert_document(conn, "EU", "INSURANCE", "repo_ver_get_out")
        in_ver = _insert_version(conn, in_doc)
        out_ver = _insert_version(conn, out_doc)

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = DocumentVersionsRepository(session)
        in_dto = await repo.get_by_id(in_ver)
        out_dto = await repo.get_by_id(out_ver)
        missing = await repo.get_by_id(uuid.uuid4())

    assert in_dto is not None and in_dto.id == in_ver
    assert out_dto is None
    assert missing is None


@pytest.mark.integration
async def test_clauses_list_for_version_ordered_by_ord(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "repo_corpus_cl_list@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        doc = _insert_document(conn, "UK", "BANKING", "repo_cl_doc")
        ver = _insert_version(conn, doc)
        c3 = _insert_clause(conn, ver, "Part 1 / Section C", 3)
        c1 = _insert_clause(conn, ver, "Part 1 / Section A", 1)
        c2 = _insert_clause(conn, ver, "Part 1 / Section B", 2)

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = ClausesRepository(session)
        clauses = await repo.list_for_version(ver)

    assert [c.id for c in clauses] == [c1, c2, c3]
    assert all(isinstance(c, ClauseDTO) for c in clauses)


@pytest.mark.integration
async def test_clauses_get_by_id_returns_dto_or_none(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    with sync.begin() as conn:
        uid = _make_user(conn, "repo_corpus_cl_get@example.com")
        _subscribe(conn, uid, [("UK", "BANKING")])
        in_doc = _insert_document(conn, "UK", "BANKING", "repo_cl_in")
        out_doc = _insert_document(conn, "EU", "INSURANCE", "repo_cl_out")
        in_ver = _insert_version(conn, in_doc)
        out_ver = _insert_version(conn, out_doc)
        in_cl = _insert_clause(conn, in_ver, "Part 1 / Section A", 1)
        out_cl = _insert_clause(conn, out_ver, "Part 1 / Section A", 1)

    async with session_for_user(async_engine, uid) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = ClausesRepository(session)
        in_dto = await repo.get_by_id(in_cl)
        out_dto = await repo.get_by_id(out_cl)
        missing = await repo.get_by_id(uuid.uuid4())

    assert in_dto is not None and in_dto.id == in_cl
    assert out_dto is None
    assert missing is None

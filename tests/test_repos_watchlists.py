"""Integration tests for ``WatchlistsRepository`` (WU1.6).

Exercises the repo under the WU1.5 session bracket plus
``SET LOCAL ROLE api_app`` so RLS is the real filter.

Coverage:

- ``create`` returns a populated ``WatchlistDTO`` (id and created_at
  filled in by Postgres defaults; user_id and name echoed).
- ``list_for`` returns the owner's rows as DTOs.
- ``get_by_id`` returns a DTO for own rows; ``None`` for unknown ids.
- ``delete`` returns ``True`` when a row matches and removes it;
  ``False`` for a non-existent id.

Cross-user assertions (B cannot see A's data) belong to the WU1.7
two-client gate at ``tests/isolation/`` — this file is the unit slice.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy
from alembic import command
from alembic.config import Config
from horizons_core.db.session import make_engine, session_for_user
from horizons_core.repos.watchlists import WatchlistDTO, WatchlistsRepository
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy import Engine
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


def _make_user(sync: Engine, email: str) -> uuid.UUID:
    with sync.begin() as conn:
        return conn.execute(
            sqlalchemy.text(
                "INSERT INTO users (email, password_hash, role) "
                "VALUES (:e, 'hash', 'client') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()


def _make_document_and_subscription(
    sync: Engine,
    user_id: uuid.UUID,
    lawstronaut_id: str,
    jurisdiction: str = "ie",
    sector: str = "legal",
) -> uuid.UUID:
    """Seed a document plus an active subscription scope covering it.

    Together these make the ``watchlists_in_subscription_scope`` trigger
    pass when the repo writes a watchlist for ``user_id``.
    """
    with sync.begin() as conn:
        doc = conn.execute(
            sqlalchemy.text(
                "INSERT INTO documents (jurisdiction, sector, lawstronaut_document_id, title) "
                "VALUES (:j, :s, :l, 'repo_wl_doc') RETURNING id"
            ),
            {"j": jurisdiction, "s": sector, "l": lawstronaut_id},
        ).scalar_one()
        sub = conn.execute(
            sqlalchemy.text(
                "INSERT INTO subscriptions (user_id, valid_from) "
                "VALUES (:u, now() - interval '1 day') RETURNING id"
            ),
            {"u": user_id},
        ).scalar_one()
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO subscription_scopes (subscription_id, jurisdiction, sector) "
                "VALUES (:s, :j, :sec)"
            ),
            {"s": sub, "j": jurisdiction, "sec": sector},
        )
    return doc


@pytest.mark.integration
async def test_create_returns_populated_dto(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    u = _make_user(sync, "repo_wl_create@example.com")
    doc = _make_document_and_subscription(sync, u, "repo_wl_create_doc")

    async with session_for_user(async_engine, u) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = WatchlistsRepository(session)
        dto = await repo.create(user_id=u, document_id=doc, name="repo_wl_created")

    assert isinstance(dto, WatchlistDTO)
    assert dto.user_id == u
    assert dto.document_id == doc
    assert dto.name == "repo_wl_created"
    assert isinstance(dto.id, uuid.UUID)
    assert dto.created_at is not None


@pytest.mark.integration
async def test_list_for_returns_owner_rows(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    u = _make_user(sync, "repo_wl_list@example.com")
    doc1 = _make_document_and_subscription(sync, u, "repo_wl_list_doc1")
    # Second document under the same subscription scope.
    with sync.begin() as conn:
        doc2 = conn.execute(
            sqlalchemy.text(
                "INSERT INTO documents (jurisdiction, sector, lawstronaut_document_id, title) "
                "VALUES ('ie', 'legal', 'repo_wl_list_doc2', 'doc2') RETURNING id"
            )
        ).scalar_one()

    async with session_for_user(async_engine, u) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = WatchlistsRepository(session)
        await repo.create(user_id=u, document_id=doc1, name="repo_wl_list_one")
        await repo.create(user_id=u, document_id=doc2, name="repo_wl_list_two")

    async with session_for_user(async_engine, u) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = WatchlistsRepository(session)
        items = sorted(w.name for w in await repo.list_for())

    assert items == ["repo_wl_list_one", "repo_wl_list_two"]


@pytest.mark.integration
async def test_get_by_id_returns_dto_or_none(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    u = _make_user(sync, "repo_wl_get@example.com")
    doc = _make_document_and_subscription(sync, u, "repo_wl_get_doc")

    async with session_for_user(async_engine, u) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = WatchlistsRepository(session)
        created = await repo.create(user_id=u, document_id=doc, name="repo_wl_get_target")

    async with session_for_user(async_engine, u) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = WatchlistsRepository(session)
        fetched = await repo.get_by_id(created.id)
        missing = await repo.get_by_id(uuid.uuid4())

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == "repo_wl_get_target"
    assert missing is None


@pytest.mark.integration
async def test_delete_owned_row_returns_true(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    u = _make_user(sync, "repo_wl_delete@example.com")
    doc = _make_document_and_subscription(sync, u, "repo_wl_delete_doc")

    async with session_for_user(async_engine, u) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = WatchlistsRepository(session)
        created = await repo.create(user_id=u, document_id=doc, name="repo_wl_delete_me")

    async with session_for_user(async_engine, u) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = WatchlistsRepository(session)
        removed = await repo.delete(user_id=u, watchlist_id=created.id)
        gone = await repo.get_by_id(created.id)

    assert removed is True
    assert gone is None


@pytest.mark.integration
async def test_delete_nonexistent_returns_false(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    sync, _ = migrated_db
    u = _make_user(sync, "repo_wl_delete_miss@example.com")

    async with session_for_user(async_engine, u) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        repo = WatchlistsRepository(session)
        removed = await repo.delete(user_id=u, watchlist_id=uuid.uuid4())

    assert removed is False

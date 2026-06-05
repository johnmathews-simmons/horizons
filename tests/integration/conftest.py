"""Fixtures for WU3.3 ingestion-worker integration tests.

Reuses the session-scoped ``postgres_container`` from
``tests/conftest.py`` (pytest's conftest hierarchy autoloads it).
Applies the Alembic tree and hands back both a sync SQLAlchemy engine
for seeding fixtures and an asyncpg DSN for the worker under test.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy import Engine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    """Strip SQLAlchemy's ``+asyncpg`` so asyncpg.connect accepts the URL."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


@dataclass(frozen=True)
class MigratedDb:
    sync_engine: Engine
    asyncpg_dsn: str


@pytest.fixture
def migrated_db(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[MigratedDb]:
    """Apply Alembic head and return sync engine + asyncpg DSN."""
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    asyncpg_dsn = _asyncpg_dsn(postgres_container.get_connection_url(driver="asyncpg"))
    monkeypatch.setenv("HORIZONS_DB_URL", sync_url)
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")

    sync_engine = create_engine(sync_url, future=True)
    try:
        # The postgres_container fixture is session-scoped — rows from
        # earlier tests persist. Truncate the WU3.1 surface before each
        # WU3.3 test to keep the inserts deterministic.
        with sync_engine.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE document_poll_schedule, ingestion_incident, "
                    "document_versions, clauses, documents "
                    "RESTART IDENTITY CASCADE"
                )
            )
        yield MigratedDb(sync_engine=sync_engine, asyncpg_dsn=asyncpg_dsn)
    finally:
        sync_engine.dispose()


@pytest_asyncio.fixture
async def pool(migrated_db: MigratedDb) -> AsyncIterator[asyncpg.Pool]:
    """Small asyncpg pool against the migrated container."""
    pool = await asyncpg.create_pool(
        dsn=migrated_db.asyncpg_dsn,
        min_size=2,
        max_size=4,
    )
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


def _sha256_32() -> bytes:
    return hashlib.sha256(b"hello-wu33").digest()


def seed_schedule(
    sync_engine: Engine,
    *,
    n_due: int = 1,
    cadence: timedelta = timedelta(hours=24),
    failure_count: int = 0,
) -> list[uuid.UUID]:
    """Insert ``n_due`` documents + due schedule rows. Returns doc IDs."""
    now = datetime.now(UTC)
    due_at = now - timedelta(minutes=1)
    doc_ids: list[uuid.UUID] = []
    with sync_engine.begin() as conn:
        for i in range(n_due):
            doc_id = conn.execute(
                text(
                    "INSERT INTO documents "
                    "(jurisdiction, sector, lawstronaut_document_id, title) "
                    "VALUES (:j, :s, :lid, :t) RETURNING id"
                ),
                {
                    "j": "IE",
                    "s": "BANKING",
                    "lid": f"WU33-{uuid.uuid4()}",
                    "t": f"WU3.3 fixture {i}",
                },
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO document_poll_schedule "
                    "(document_id, cadence_interval, next_poll_at, failure_count) "
                    "VALUES (:d, :c, :n, :f)"
                ),
                {"d": doc_id, "c": cadence, "n": due_at, "f": failure_count},
            )
            doc_ids.append(doc_id)
    return doc_ids


def fetch_schedule_row(sync_engine: Engine, document_id: uuid.UUID) -> dict[str, object]:
    with sync_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT failure_count, last_polled_at, next_poll_at "
                "FROM document_poll_schedule WHERE document_id = :d"
            ),
            {"d": document_id},
        ).one()
    return {
        "failure_count": row.failure_count,
        "last_polled_at": row.last_polled_at,
        "next_poll_at": row.next_poll_at,
    }


def fetch_incidents(sync_engine: Engine, document_id: uuid.UUID) -> list[dict[str, object]]:
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT error_class, payload "
                "FROM ingestion_incident WHERE document_id = :d "
                "ORDER BY id"
            ),
            {"d": document_id},
        ).all()
    return [{"error_class": r.error_class, "payload": r.payload} for r in rows]


# Re-export for convenience.
__all__ = [
    "MigratedDb",
    "_asyncpg_dsn",
    "_sha256_32",
    "asyncio",
    "fetch_incidents",
    "fetch_schedule_row",
    "seed_schedule",
]

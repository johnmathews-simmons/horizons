"""Shared fixtures for the WU1.7 two-client isolation gate.

This is the gate test set per the improvement plan — no Track 2 / 3 / 4
work should merge while these tests are red. They prove the two
multi-tenant isolation axes (cross-client privacy and subscription
scope) hold end-to-end through the repository layer.

The ``two_clients`` fixture seeds:

- User A with a UK / BANKING-only subscription
- User B with an EU / INSURANCE-only subscription
- One watchlist owned by each
- One in-scope document/version/clause for each scope
- An admin user (``role='admin'``) used by the WU1.9 admin code paths.
  The user only needs to exist so the ``admin_access_log.admin_id`` FK
  resolves; no subscription is attached.

And returns a ``TwoClients`` object whose ``session_for(user_id)`` helper
hands out an async session already bracketed with the WU1.5 GUC binding
plus ``SET LOCAL ROLE api_app`` — the same shape Track 4's FastAPI
request scope will use.

All fixtures here are function-scoped because SQLAlchemy's asyncpg
engine binds to the event loop of the first test that uses it; sharing
across pytest-asyncio's function-scoped loops produces
``InterfaceError: another operation is in progress``. Re-seeding per
test is cheap at this dataset size and matches the pattern used by
``test_session_bracket.py``.
"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy
from alembic import command
from alembic.config import Config
from horizons_core.db.session import make_engine, session_for_user
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Iterator

    from sqlalchemy import Connection, Engine
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[2]
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


@dataclass(frozen=True, slots=True)
class TwoClients:
    """The artefacts the gate tests need to assert against."""

    a_id: uuid.UUID
    b_id: uuid.UUID
    admin_id: uuid.UUID
    a_watchlist_id: uuid.UUID
    b_watchlist_id: uuid.UUID
    a_document_id: uuid.UUID  # UK / BANKING
    b_document_id: uuid.UUID  # EU / INSURANCE
    a_version_id: uuid.UUID
    b_version_id: uuid.UUID
    a_clause_id: uuid.UUID
    b_clause_id: uuid.UUID

    async_engine: AsyncEngine

    @asynccontextmanager
    async def session_for(self, user_id: uuid.UUID) -> AsyncGenerator[AsyncSession]:
        """The Track-4-shaped session: GUC bound + role assumed as api_app."""
        async with session_for_user(self.async_engine, user_id) as session:
            await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
            yield session

    @asynccontextmanager
    async def admin_session(self) -> AsyncGenerator[AsyncSession]:
        """Admin-bypass session for the cross-tenant assertion.

        Uses a throwaway ``user_id`` because ``session_for_user`` requires
        one; ``admin_bypass`` ignores the GUC.
        """
        async with session_for_user(self.async_engine, uuid.uuid4()) as session:
            await session.execute(sqlalchemy.text("SET LOCAL ROLE admin_bypass"))
            yield session


def _sha256() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _make_user(conn: Connection, email: str, role: str = "client") -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO users (email, password_hash, role) VALUES (:e, 'hash', :r) RETURNING id"
        ),
        {"e": email, "r": role},
    ).scalar_one()


def _subscribe(
    conn: Connection,
    user_id: uuid.UUID,
    jurisdiction: str,
    sector: str,
) -> None:
    now = datetime.now(UTC)
    sid = conn.execute(
        sqlalchemy.text(
            "INSERT INTO subscriptions (user_id, valid_from, valid_to) "
            "VALUES (:u, :f, NULL) RETURNING id"
        ),
        {"u": user_id, "f": now - timedelta(days=30)},
    ).scalar_one()
    conn.execute(
        sqlalchemy.text(
            "INSERT INTO subscription_scopes "
            "(subscription_id, jurisdiction, sector) "
            "VALUES (:s, :j, :sec)"
        ),
        {"s": sid, "j": jurisdiction, "sec": sector},
    )


def _make_watchlist(
    conn: Connection,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    name: str,
) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO watchlists (user_id, document_id, name) VALUES (:u, :d, :n) RETURNING id"
        ),
        {"u": user_id, "d": document_id, "n": name},
    ).scalar_one()


def _make_doc_chain(
    conn: Connection,
    jurisdiction: str,
    sector: str,
    label: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    doc_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "lid": f"isolation_{label}_{uuid.uuid4()}",
            "t": f"isolation_{label}",
        },
    ).scalar_one()
    ver_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO document_versions "
            "(document_id, version_label, publication_date, effective_date, "
            "content_blob_container, content_blob_key, content_sha256, "
            "content_bytes) "
            "VALUES (:d, 'v1', :p, :e, 'isolation', :k, :h, 100) RETURNING id"
        ),
        {
            "d": doc_id,
            "p": datetime.now(UTC),
            "e": datetime.now(UTC),
            "k": f"{label}/v1.md",
            "h": _sha256(),
        },
    ).scalar_one()
    cl_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO clauses "
            "(document_version_id, clause_uid, clause_path, text_content, ord) "
            "VALUES (:v, :u, 'Part 1 / Section 1', :t, 1) RETURNING id"
        ),
        {
            "v": ver_id,
            "u": uuid.uuid4(),
            "t": f"isolation_{label} clause body",
        },
    ).scalar_one()
    return doc_id, ver_id, cl_id


@pytest_asyncio.fixture
async def two_clients(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> TwoClients:
    sync, _ = migrated_db
    # Per-test unique suffix lets the function-scoped seeding coexist
    # with rows left over from earlier tests in the same Postgres
    # session.
    suffix = uuid.uuid4().hex[:8]
    with sync.begin() as conn:
        a_id = _make_user(conn, f"isolation_a_{suffix}@example.com")
        b_id = _make_user(conn, f"isolation_b_{suffix}@example.com")
        admin_id = _make_user(conn, f"isolation_admin_{suffix}@example.com", role="admin")
        _subscribe(conn, a_id, "UK", "BANKING")
        _subscribe(conn, b_id, "EU", "INSURANCE")
        a_doc, a_ver, a_cl = _make_doc_chain(conn, "UK", "BANKING", f"a_{suffix}")
        b_doc, b_ver, b_cl = _make_doc_chain(conn, "EU", "INSURANCE", f"b_{suffix}")
        # Watchlists land after documents now — the FK + the WU4.3
        # scope trigger both need a document target. Each user watches
        # their own scope's document so the trigger (if it ran under
        # api_app) would pass; under the superuser seeding it
        # short-circuits regardless.
        a_wid = _make_watchlist(conn, a_id, a_doc, f"isolation_a_watchlist_{suffix}")
        b_wid = _make_watchlist(conn, b_id, b_doc, f"isolation_b_watchlist_{suffix}")

    return TwoClients(
        a_id=a_id,
        b_id=b_id,
        admin_id=admin_id,
        a_watchlist_id=a_wid,
        b_watchlist_id=b_wid,
        a_document_id=a_doc,
        b_document_id=b_doc,
        a_version_id=a_ver,
        b_version_id=b_ver,
        a_clause_id=a_cl,
        b_clause_id=b_cl,
        async_engine=async_engine,
    )

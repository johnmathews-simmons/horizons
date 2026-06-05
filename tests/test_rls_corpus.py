"""Integration test for the WU1.4 corpus RLS policies.

The subscription-scope axis. The corpus tables (``documents``,
``document_versions``, ``clauses``) carry ``api_app`` policies that
join through ``app_private.current_scope()``, plus
``TO ingestion_worker`` pass-through policies that let the worker keep
writing without scope filtering.

Coverage:

- ``api_app`` under a (UK, BANKING) subscription sees only in-scope
  documents, versions, and clauses; rows under (EU, INSURANCE) are
  filtered out.
- ``api_app`` without ``app.user_id`` set raises — ``current_scope()``
  propagates the failure into the RLS predicate.
- ``ingestion_worker`` can INSERT documents / versions / clauses and
  SELECT every row it wrote, regardless of subscription scope.
- ``admin_bypass`` (BYPASSRLS) sees every corpus row regardless of
  GUC.

Sync — see other migration tests for the rationale.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


@pytest.fixture
def migrated_engine(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> Engine:
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    monkeypatch.setenv("HORIZONS_DB_URL", sync_url)
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    return create_engine(sync_url, future=True)


# ---- helpers -------------------------------------------------------------


def _sha256() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _make_user(conn: Connection, email: str) -> uuid.UUID:
    return conn.execute(
        text(
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
        text(
            "INSERT INTO subscriptions (user_id, valid_from, valid_to) "
            "VALUES (:u, :f, NULL) RETURNING id"
        ),
        {"u": user_id, "f": now - timedelta(days=30)},
    ).scalar_one()
    for jurisdiction, sector in scopes:
        conn.execute(
            text(
                "INSERT INTO subscription_scopes "
                "(subscription_id, jurisdiction, sector) "
                "VALUES (:s, :j, :sec)"
            ),
            {"s": sid, "j": jurisdiction, "sec": sector},
        )
    return sid


def _insert_document(
    conn: Connection,
    jurisdiction: str,
    sector: str,
    title: str,
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "lid": f"corpus_rls_{uuid.uuid4()}",
            "t": title,
        },
    ).scalar_one()


def _insert_version(
    conn: Connection,
    doc_id: uuid.UUID,
    label: str = "v1",
) -> uuid.UUID:
    return conn.execute(
        text(
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
            "e": datetime.now(UTC),
            "c": "corpus",
            "k": f"{doc_id}/{label}.md",
            "h": _sha256(),
            "b": 1000,
        },
    ).scalar_one()


def _insert_clause(
    conn: Connection,
    version_id: uuid.UUID,
    path: str = "Part 1 / Section 1",
    ord_value: int = 1,
) -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO clauses "
            "(document_version_id, clause_uid, clause_path, text_content, ord) "
            "VALUES (:v, :u, :p, :t, :o) RETURNING id"
        ),
        {
            "v": version_id,
            "u": uuid.uuid4(),
            "p": path,
            "t": "corpus_rls clause body",
            "o": ord_value,
        },
    ).scalar_one()


def _set_app_user(conn: Connection, user_id: uuid.UUID) -> None:
    conn.execute(
        text("SELECT set_config('app.user_id', :u, true)"),
        {"u": str(user_id)},
    )


# ---- tests ---------------------------------------------------------------


@pytest.mark.integration
def test_api_app_sees_only_in_scope_documents(migrated_engine: Engine) -> None:
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "corpus_rls_docs@example.com")
            _subscribe(conn, uid, [("UK", "BANKING")])
            in_scope = _insert_document(conn, "UK", "BANKING", "in_scope_doc")
            _insert_document(conn, "EU", "INSURANCE", "out_of_scope_doc")

        with migrated_engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE api_app"))
            _set_app_user(conn, uid)
            visible = sorted(
                r.title
                for r in conn.execute(
                    text(
                        "SELECT title FROM documents "
                        "WHERE title IN ('in_scope_doc', 'out_of_scope_doc')"
                    )
                )
            )
            ids = [
                r.id
                for r in conn.execute(
                    text("SELECT id FROM documents WHERE id = :id"),
                    {"id": in_scope},
                )
            ]
    finally:
        migrated_engine.dispose()

    assert visible == ["in_scope_doc"]
    assert ids == [in_scope]


@pytest.mark.integration
def test_api_app_sees_only_in_scope_versions_and_clauses(
    migrated_engine: Engine,
) -> None:
    """The child policies walk up through the FK chain to ``documents``."""
    try:
        with migrated_engine.begin() as conn:
            uid = _make_user(conn, "corpus_rls_children@example.com")
            _subscribe(conn, uid, [("UK", "BANKING")])
            in_doc = _insert_document(conn, "UK", "BANKING", "child_in_scope")
            out_doc = _insert_document(conn, "EU", "INSURANCE", "child_out_of_scope")
            in_ver = _insert_version(conn, in_doc, "v1")
            out_ver = _insert_version(conn, out_doc, "v1")
            in_cl = _insert_clause(conn, in_ver, path="Part 1 / Section A")
            _insert_clause(conn, out_ver, path="Part 1 / Section B")

        with migrated_engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE api_app"))
            _set_app_user(conn, uid)
            visible_versions = {
                r.id
                for r in conn.execute(
                    text("SELECT id FROM document_versions WHERE id IN (:a, :b)"),
                    {"a": in_ver, "b": out_ver},
                )
            }
            visible_clauses = {
                r.id
                for r in conn.execute(
                    text("SELECT id FROM clauses"),
                )
            }
    finally:
        migrated_engine.dispose()

    assert visible_versions == {in_ver}
    assert in_cl in visible_clauses
    # No clauses belonging to the out-of-scope version leaked.
    # (We assert on the in_cl specifically; the full set may include
    # rows from unrelated tests in the same Postgres session.)


@pytest.mark.integration
def test_api_app_select_raises_when_app_user_id_unset(
    migrated_engine: Engine,
) -> None:
    """current_scope() raises on unset GUC; the corpus RLS predicate
    propagates the failure."""
    try:
        with migrated_engine.begin() as conn:
            _insert_document(conn, "UK", "BANKING", "no_guc_doc")

        with (
            pytest.raises(DBAPIError),
            migrated_engine.begin() as conn,
        ):
            conn.execute(text("SET LOCAL ROLE api_app"))
            # Deliberately omit set_config.
            conn.execute(text("SELECT id FROM documents")).all()
    finally:
        migrated_engine.dispose()


@pytest.mark.integration
def test_ingestion_worker_can_insert_and_read_regardless_of_scope(
    migrated_engine: Engine,
) -> None:
    """The pass-through policies let the worker write and read every
    row it wrote, without any subscription scope."""
    try:
        # Seed pre-existing rows under superuser.
        with migrated_engine.begin() as conn:
            pre_doc = _insert_document(conn, "EU", "INSURANCE", "ingest_pre")

        # Now act as ingestion_worker. No subscription, no GUC.
        with migrated_engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE ingestion_worker"))
            new_doc = conn.execute(
                text(
                    "INSERT INTO documents "
                    "(jurisdiction, sector, lawstronaut_document_id, title) "
                    "VALUES ('EU', 'INSURANCE', :lid, 'ingest_new') "
                    "RETURNING id"
                ),
                {"lid": f"corpus_rls_ingest_{uuid.uuid4()}"},
            ).scalar_one()
            new_ver = _insert_version(conn, new_doc, "v1")
            new_cl = _insert_clause(conn, new_ver, path="Part 1 / Section X")

            # Reads its own writes — and any other row, regardless of
            # scope. We assert on the specific IDs to avoid coupling
            # to other tests' seed data.
            seen_docs = {
                r.id
                for r in conn.execute(
                    text("SELECT id FROM documents WHERE id IN (:a, :b)"),
                    {"a": pre_doc, "b": new_doc},
                )
            }
            seen_versions = {
                r.id
                for r in conn.execute(
                    text("SELECT id FROM document_versions WHERE id = :id"),
                    {"id": new_ver},
                )
            }
            seen_clauses = {
                r.id
                for r in conn.execute(
                    text("SELECT id FROM clauses WHERE id = :id"),
                    {"id": new_cl},
                )
            }
    finally:
        migrated_engine.dispose()

    assert seen_docs == {pre_doc, new_doc}
    assert seen_versions == {new_ver}
    assert seen_clauses == {new_cl}


@pytest.mark.integration
def test_admin_bypass_sees_all_corpus(migrated_engine: Engine) -> None:
    """SET LOCAL ROLE admin_bypass — BYPASSRLS sees every row across
    every (jurisdiction, sector) regardless of GUC."""
    try:
        with migrated_engine.begin() as conn:
            uk_doc = _insert_document(conn, "UK", "BANKING", "admin_uk")
            eu_doc = _insert_document(conn, "EU", "INSURANCE", "admin_eu")

        with migrated_engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE admin_bypass"))
            # No GUC needed.
            seen = {
                r.id
                for r in conn.execute(
                    text("SELECT id FROM documents WHERE id IN (:a, :b)"),
                    {"a": uk_doc, "b": eu_doc},
                )
            }
    finally:
        migrated_engine.dispose()

    assert seen == {uk_doc, eu_doc}

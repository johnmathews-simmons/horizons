"""WU1.8 — Hypothesis property test for isolation across N clients.

Generalises the WU1.7 two-client gate to ``N ∈ [2, 5]`` clients, each
with ``M ∈ [1, 3]`` subscription scopes drawn from a small
``jurisdiction × sector`` alphabet, and a Hypothesis-chosen number of
private-state writes (watchlists) and corpus-row writes
(document/version/clause chains tagged with one of that client's own
scopes).

The assertion is the universal isolation invariant: every row a client
reads through the repository layer must satisfy one of two predicates —

- **Private state** (``WatchlistsRepository``): ``row.user_id ==
  client.user_id``.
- **Corpus** (``DocumentsRepository`` / ``DocumentVersionsRepository`` /
  ``ClausesRepository``): the row's ``(jurisdiction, sector)`` is one
  of ``client.scopes``.

Any counterexample is a defence-in-depth break somewhere in
session bracket → ``SET LOCAL ROLE`` → RLS policy → repository.

Slow by design: ≈25 Hypothesis examples × N-client seeding against a
real Postgres. The ``nightly`` marker keeps the test out of the default
``uv run pytest`` invocation (``addopts`` adds ``-m 'not nightly'``); the
GitHub Actions ``nightly.yml`` workflow flips it on.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import sqlalchemy
from horizons_core.db.session import session_for_user
from horizons_core.repos.clauses import ClausesRepository
from horizons_core.repos.documents import DocumentsRepository
from horizons_core.repos.versions import DocumentVersionsRepository
from horizons_core.repos.watchlists import WatchlistsRepository
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine
    from sqlalchemy.ext.asyncio import AsyncEngine


JURISDICTIONS = ("UK", "EU", "US", "IE")
SECTORS = ("BANKING", "INSURANCE", "ENERGY", "TECH")
ScopeTuple = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ClientBlueprint:
    """Hypothesis-generated shape for a single client."""

    scopes: frozenset[ScopeTuple]
    n_watchlists: int
    doc_scopes: tuple[ScopeTuple, ...]


@dataclass(frozen=True, slots=True)
class SeededDoc:
    doc_id: uuid.UUID
    version_id: uuid.UUID
    clause_id: uuid.UUID
    scope: ScopeTuple


@dataclass(frozen=True, slots=True)
class SeededClient:
    user_id: uuid.UUID
    scopes: frozenset[ScopeTuple]
    watchlist_ids: frozenset[uuid.UUID]
    docs: tuple[SeededDoc, ...]


@st.composite
def _client_blueprint(draw: st.DrawFn) -> ClientBlueprint:
    scopes: frozenset[ScopeTuple] = draw(
        st.frozensets(
            st.tuples(
                st.sampled_from(JURISDICTIONS),
                st.sampled_from(SECTORS),
            ),
            min_size=1,
            max_size=3,
        )
    )
    n_watch = draw(st.integers(min_value=0, max_value=3))
    n_docs = draw(st.integers(min_value=0, max_value=3))
    scope_list = sorted(scopes)
    doc_scopes = tuple(draw(st.sampled_from(scope_list)) for _ in range(n_docs))
    return ClientBlueprint(scopes=scopes, n_watchlists=n_watch, doc_scopes=doc_scopes)


@st.composite
def _isolation_plan(draw: st.DrawFn) -> tuple[ClientBlueprint, ...]:
    n = draw(st.integers(min_value=2, max_value=5))
    return tuple(draw(_client_blueprint()) for _ in range(n))


def _sha256() -> bytes:
    return hashlib.sha256(uuid.uuid4().bytes).digest()


def _seed_user(conn: Connection, email: str) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO users (email, password_hash, role) "
            "VALUES (:e, 'hash', 'client') RETURNING id"
        ),
        {"e": email},
    ).scalar_one()


def _seed_subscription(
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


def _seed_watchlist(conn: Connection, user_id: uuid.UUID, name: str) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text("INSERT INTO watchlists (user_id, name) VALUES (:u, :n) RETURNING id"),
        {"u": user_id, "n": name},
    ).scalar_one()


def _seed_doc_chain(
    conn: Connection,
    jurisdiction: str,
    sector: str,
    label: str,
) -> SeededDoc:
    doc_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {
            "j": jurisdiction,
            "s": sector,
            "lid": f"property_{label}_{uuid.uuid4()}",
            "t": f"property_{label}",
        },
    ).scalar_one()
    ver_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO document_versions "
            "(document_id, version_label, publication_date, effective_date, "
            "content_blob_container, content_blob_key, content_sha256, "
            "content_bytes) "
            "VALUES (:d, 'v1', :p, :e, 'property', :k, :h, 100) RETURNING id"
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
            "t": f"property_{label} clause body",
        },
    ).scalar_one()
    return SeededDoc(
        doc_id=doc_id,
        version_id=ver_id,
        clause_id=cl_id,
        scope=(jurisdiction, sector),
    )


def _apply_plan(
    sync_engine: Engine,
    plan: tuple[ClientBlueprint, ...],
) -> tuple[SeededClient, ...]:
    # Per-example suffix lets every Hypothesis example coexist in the
    # same migrated DB — the universal invariant is shape-independent of
    # what other rows exist, so we keep the function-scoped Postgres
    # warm and namespace each example by suffix.
    suffix = uuid.uuid4().hex[:12]
    seeded: list[SeededClient] = []
    with sync_engine.begin() as conn:
        for i, bp in enumerate(plan):
            user_id = _seed_user(conn, f"property_{suffix}_c{i}@example.com")
            for j, s in bp.scopes:
                _seed_subscription(conn, user_id, j, s)

            watchlist_ids = frozenset(
                _seed_watchlist(conn, user_id, f"property_{suffix}_c{i}_w{w}")
                for w in range(bp.n_watchlists)
            )

            docs = tuple(
                _seed_doc_chain(conn, jur, sec, f"{suffix}_c{i}_d{d_idx}")
                for d_idx, (jur, sec) in enumerate(bp.doc_scopes)
            )

            seeded.append(
                SeededClient(
                    user_id=user_id,
                    scopes=bp.scopes,
                    watchlist_ids=watchlist_ids,
                    docs=docs,
                )
            )
    return tuple(seeded)


@pytest.mark.integration
@pytest.mark.nightly
@given(plan=_isolation_plan())
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_isolation_holds_under_arbitrary_writes(
    plan: tuple[ClientBlueprint, ...],
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
) -> None:
    """Universal isolation invariant under arbitrary clients × scopes × writes.

    For each generated client:

    1. ``WatchlistsRepository.list_for`` returns only watchlists owned
       by that client; every watchlist that client created is visible;
       a direct ``get_by_id`` on someone else's watchlist returns
       ``None``.
    2. ``DocumentsRepository.list_all`` returns only documents whose
       ``(jurisdiction, sector)`` is in that client's scopes. The same
       scope predicate holds transitively for
       ``DocumentVersionsRepository.get_by_id`` and
       ``ClausesRepository.get_by_id`` — out-of-scope versions /
       clauses are filtered through the FK chain, in-scope ones (from
       any client) are visible.
    """
    sync_engine, _ = migrated_db
    seeded = _apply_plan(sync_engine, plan)

    for me in seeded:
        my_scopes = me.scopes
        my_watchlist_ids = me.watchlist_ids

        async with session_for_user(async_engine, me.user_id) as session:
            await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))

            # ── Cross-client privacy axis (watchlists) ────────────
            wl_repo = WatchlistsRepository(session)
            visible_wls = await wl_repo.list_for()

            for wl in visible_wls:
                assert wl.user_id == me.user_id, (
                    f"Watchlist {wl.id} (owner {wl.user_id}) leaked to client {me.user_id}"
                )

            visible_wl_ids = {w.id for w in visible_wls}
            missing = my_watchlist_ids - visible_wl_ids
            assert not missing, f"Client {me.user_id} cannot see own watchlists: {missing}"

            for other in seeded:
                if other.user_id == me.user_id:
                    continue
                for wl_id in other.watchlist_ids:
                    assert await wl_repo.get_by_id(wl_id) is None, (
                        f"Watchlist {wl_id} of client {other.user_id} "
                        f"leaked to {me.user_id} via get_by_id"
                    )

            # ── Subscription-scope axis (corpus) ──────────────────
            doc_repo = DocumentsRepository(session)
            ver_repo = DocumentVersionsRepository(session)
            cl_repo = ClausesRepository(session)

            visible_docs = await doc_repo.list_all()
            for doc in visible_docs:
                assert (doc.jurisdiction, doc.sector) in my_scopes, (
                    f"Document {doc.id} ({doc.jurisdiction}, {doc.sector}) "
                    f"leaked to client {me.user_id} with scopes {my_scopes}"
                )

            visible_doc_ids = {d.id for d in visible_docs}

            for other in seeded:
                for d in other.docs:
                    in_my_scope = d.scope in my_scopes
                    if in_my_scope:
                        assert d.doc_id in visible_doc_ids, (
                            f"In-scope document {d.doc_id} {d.scope} "
                            f"invisible to client {me.user_id} {my_scopes}"
                        )
                        assert await doc_repo.get_by_id(d.doc_id) is not None
                        assert await ver_repo.get_by_id(d.version_id) is not None
                        assert await cl_repo.get_by_id(d.clause_id) is not None
                    else:
                        assert d.doc_id not in visible_doc_ids, (
                            f"Out-of-scope document {d.doc_id} {d.scope} "
                            f"visible to client {me.user_id} {my_scopes}"
                        )
                        assert await doc_repo.get_by_id(d.doc_id) is None
                        assert await ver_repo.get_by_id(d.version_id) is None
                        assert await cl_repo.get_by_id(d.clause_id) is None

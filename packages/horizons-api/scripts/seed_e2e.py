#!/usr/bin/env python
"""Seed (or tear down) e2e fixtures for the Playwright smoke test (WU8.2).

The Playwright test in ``packages/horizons-webapp/e2e`` exercises:

  login as UK client -> /changes -> click MODIFIED row -> assert
  before/after text + 0.92 confidence badge -> logout -> login as EU
  client -> /changes -> assert different events visible.

This script seeds exactly the rows that flow needs and nothing else:

* Two ``users`` rows (one UK client, one EU client) under the
  ``@e2e.example.com`` reserved TLD so they are trivially identifiable.
* One ``subscriptions`` + ``subscription_scopes`` row per user, scoped
  to (UK, BANKING) and (EU, BANKING) respectively. Disjoint scopes
  are the substrate that proves subscription RLS in the UI.
* Two synthetic documents (UK + EU) with one ``document_versions``
  row each. Synthetic content; never round-trips to Lawstronaut.
* Three ``change_events`` rows:

  1. UK / MODIFIED / confidence 0.92 (high, green badge) — visible to
     UK only. Primary assertion: the test clicks this row and expects
     the badge text "0.92".
  2. EU / MODIFIED / confidence 0.78 (medium, amber badge) — visible
     to EU only. Proves scope filtering: the UK client should NOT see
     this row in /changes.
  3. UK / MOVED / confidence 0.95 — suppressed by default in the
     /changes UI (the ``Show MOVED`` toggle is off). The test asserts
     this row is NOT visible without the toggle.

Idempotence: every run begins with a teardown of anything tagged
``@e2e.example.com`` (users) or ``e2e_`` (documents). Re-running is safe.
``--teardown`` removes the fixtures without re-seeding.

The teardown bypasses the append-only triggers on
``change_events`` via ``SET LOCAL session_replication_role = 'replica'``
(superuser-only). In CI we connect as the ``postgres`` superuser of the
``services: postgres`` container; locally the dev DB superuser plays
the same role. No production database should ever run this script.

Run from the repo root:

    HORIZONS_DB_URL=postgresql+psycopg://postgres:postgres@localhost:5432/horizons \\
        uv run packages/horizons-api/scripts/seed_e2e.py

Both ``+psycopg`` and ``+asyncpg`` forms of ``HORIZONS_DB_URL`` are
accepted; the script rewrites to psycopg internally so the same env
var that uvicorn reads works here unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import sqlalchemy
from horizons_core.core.auth import hash_password
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy import Connection


# example.com is RFC-2606 reserved; pydantic's EmailStr accepts it but
# rejects RFC-6761 .test as a special-use name, so the original choice
# would fail validation on /v1/auth/login.
UK_EMAIL = "uk-client@e2e.example.com"
UK_PASSWORD = "e2e-test-pass-uk"  # noqa: S105  # fixture password for e2e
EU_EMAIL = "eu-client@e2e.example.com"
EU_PASSWORD = "e2e-test-pass-eu"  # noqa: S105

E2E_EMAIL_LIKE = "%@e2e.example.com"
E2E_DOC_LID_LIKE = "e2e\\_%"  # SQL LIKE: literal underscore needs escaping
E2E_DOC_LID_ESCAPE = "\\"

UK_DOC_LID = "e2e_uk_banking_act_v1"
EU_DOC_LID = "e2e_eu_banking_directive_v1"

# Content snippets — synthetic, no real bank or firm names. These
# strings are the before/after text stored in change_events; the
# side-by-side document viewer renders the full v1 and v2 clauses
# verbatim and the parent `DocumentDetailView` auto-scrolls + highlights
# the matched clause based on the ?before=&after= URL query params.
UK_MODIFIED_BEFORE = (
    "Article 12. The institution shall maintain a minimum capital adequacy ratio "
    "of 8 percent of risk-weighted assets at all times."
)
UK_MODIFIED_AFTER = (
    "Article 12. The institution shall maintain a minimum capital adequacy ratio "
    "of 10.5 percent of risk-weighted assets at all times."
)
EU_MODIFIED_BEFORE = (
    "Clause 4.2. Liquidity coverage shall meet 80 percent of net cash outflows "
    "over a 30 day stress horizon."
)
EU_MODIFIED_AFTER = (
    "Clause 4.2. Liquidity coverage shall meet 100 percent of net cash outflows "
    "over a 30 day stress horizon."
)
UK_MOVED_TEXT = (
    "Section 14. Reporting obligations apply to all in-scope institutions on a "
    "quarterly basis."
)


def _normalise_db_url(raw: str) -> str:
    """Convert any ``HORIZONS_DB_URL`` driver hint to sync ``+psycopg``.

    The API uses ``+asyncpg``; alembic and this script use ``+psycopg``.
    Sharing one env var across all three is the convention everywhere
    else in the repo, so we accept whichever form is in the environment
    and rewrite to psycopg here.
    """
    if "+asyncpg" in raw:
        return raw.replace("+asyncpg", "+psycopg")
    if raw.startswith("postgresql://") or raw.startswith("postgres://"):
        scheme, rest = raw.split("://", 1)
        return f"{scheme.replace('postgres', 'postgresql')}+psycopg://{rest}"
    return raw


def _teardown(conn: Connection) -> None:
    """Remove every e2e fixture row. Safe to run when nothing exists."""
    # session_replication_role = 'replica' bypasses non-system triggers
    # for this transaction only. Required because change_events has a
    # BEFORE DELETE trigger that unconditionally raises (append-only).
    conn.execute(sqlalchemy.text("SET LOCAL session_replication_role = 'replica'"))

    params_doc = {"p": "e2e_%", "esc": E2E_DOC_LID_ESCAPE}
    params_email = {"p": E2E_EMAIL_LIKE}

    conn.execute(
        sqlalchemy.text(
            "DELETE FROM change_events WHERE document_id IN ("
            "  SELECT id FROM documents WHERE lawstronaut_document_id LIKE :p ESCAPE :esc"
            ")"
        ),
        params_doc,
    )
    conn.execute(
        sqlalchemy.text(
            "DELETE FROM clauses WHERE document_version_id IN ("
            "  SELECT dv.id FROM document_versions dv "
            "  JOIN documents d ON d.id = dv.document_id "
            "  WHERE d.lawstronaut_document_id LIKE :p ESCAPE :esc"
            ")"
        ),
        params_doc,
    )
    conn.execute(
        sqlalchemy.text(
            "DELETE FROM document_versions WHERE document_id IN ("
            "  SELECT id FROM documents WHERE lawstronaut_document_id LIKE :p ESCAPE :esc"
            ")"
        ),
        params_doc,
    )
    conn.execute(
        sqlalchemy.text(
            "DELETE FROM documents WHERE lawstronaut_document_id LIKE :p ESCAPE :esc"
        ),
        params_doc,
    )
    conn.execute(
        sqlalchemy.text(
            "DELETE FROM subscription_scopes WHERE subscription_id IN ("
            "  SELECT s.id FROM subscriptions s "
            "  JOIN users u ON u.id = s.user_id "
            "  WHERE u.email LIKE :p"
            ")"
        ),
        params_email,
    )
    conn.execute(
        sqlalchemy.text(
            "DELETE FROM subscriptions WHERE user_id IN ("
            "  SELECT id FROM users WHERE email LIKE :p"
            ")"
        ),
        params_email,
    )
    # admin_access_log has two ON DELETE RESTRICT FKs to users (admin_id,
    # target_user_id). In CI the service container is fresh per run, but
    # local dev DBs survive between runs and an earlier admin code path
    # (WU4.5+) may have written a row referencing one of the e2e users.
    # Without this step a re-run on a non-fresh DB blows up on the
    # users DELETE with a 23503 FK violation.
    conn.execute(
        sqlalchemy.text(
            "DELETE FROM admin_access_log WHERE admin_id IN ("
            "  SELECT id FROM users WHERE email LIKE :p"
            ") OR target_user_id IN ("
            "  SELECT id FROM users WHERE email LIKE :p"
            ")"
        ),
        params_email,
    )
    conn.execute(
        sqlalchemy.text("DELETE FROM users WHERE email LIKE :p"),
        params_email,
    )


def _make_user(conn: Connection, email: str, password: str) -> uuid.UUID:
    return conn.execute(
        sqlalchemy.text(
            "INSERT INTO users (email, password_hash, role) "
            "VALUES (:e, :ph, 'client') RETURNING id"
        ),
        {"e": email, "ph": hash_password(password)},
    ).scalar_one()


def _subscribe(
    conn: Connection,
    user_id: uuid.UUID,
    jurisdiction: str,
    sector: str,
) -> None:
    valid_from = datetime.now(UTC) - timedelta(days=30)
    sub_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO subscriptions (user_id, valid_from, valid_to) "
            "VALUES (:u, :f, NULL) RETURNING id"
        ),
        {"u": user_id, "f": valid_from},
    ).scalar_one()
    conn.execute(
        sqlalchemy.text(
            "INSERT INTO subscription_scopes "
            "(subscription_id, jurisdiction, sector, valid_to) "
            "VALUES (:s, :j, :sec, NULL)"
        ),
        {"s": sub_id, "j": jurisdiction, "sec": sector},
    )


def _make_doc_with_versions(
    conn: Connection,
    lid: str,
    jurisdiction: str,
    sector: str,
    title: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create a document with v1 + v2.

    v1 is published 30 days ago, v2 today; the side-by-side viewer sorts
    panes by ``effective_date`` so v1 lands left and v2 right. Returns
    ``(document_id, v1_id, v2_id)``.
    """
    doc_id = conn.execute(
        sqlalchemy.text(
            "INSERT INTO documents "
            "(jurisdiction, sector, lawstronaut_document_id, title) "
            "VALUES (:j, :s, :lid, :t) RETURNING id"
        ),
        {"j": jurisdiction, "s": sector, "lid": lid, "t": title},
    ).scalar_one()
    now = datetime.now(UTC)
    earlier = now - timedelta(days=30)

    def _insert_version(label: str, when: datetime) -> uuid.UUID:
        return conn.execute(
            sqlalchemy.text(
                "INSERT INTO document_versions "
                "(document_id, version_label, publication_date, effective_date, "
                "content_blob_container, content_blob_key, content_sha256, "
                "content_bytes) "
                "VALUES (:d, :lbl, :p, :e, 'e2e', :k, :h, :b) RETURNING id"
            ),
            {
                "d": doc_id,
                "lbl": label,
                "p": when,
                "e": when,
                "k": f"{lid}/{label}.md",
                "h": hashlib.sha256(f"{lid}/{label}".encode()).digest(),
                "b": 1024,
            },
        ).scalar_one()

    v1_id = _insert_version("v1", earlier)
    v2_id = _insert_version("v2", now)
    return doc_id, v1_id, v2_id


def _insert_clauses(
    conn: Connection,
    version_id: uuid.UUID,
    clauses: list[tuple[str, str]],
) -> None:
    """Insert ``(clause_path, text_content)`` pairs in order.

    Used by the WU8.5 documents-viewer e2e: the structure-overlay toggle
    needs real clauses to render.
    """
    for ord_value, (path, text_content) in enumerate(clauses, start=1):
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO clauses "
                "(document_version_id, clause_uid, clause_path, text_content, ord) "
                "VALUES (:v, :u, :p, :t, :o)"
            ),
            {
                "v": version_id,
                "u": uuid.uuid4(),
                "p": path,
                "t": text_content,
                "o": ord_value,
            },
        )


def _emit_change_event(
    conn: Connection,
    document_id: uuid.UUID,
    document_version_id: uuid.UUID,
    jurisdiction: str,
    sector: str,
    change_type: str,
    before_path: str,
    after_path: str,
    before_text: str | None,
    after_text: str | None,
    alignment_confidence: float,
) -> None:
    now = datetime.now(UTC)
    conn.execute(
        sqlalchemy.text(
            "INSERT INTO change_events ("
            "  document_id, document_version_id, jurisdiction, sector, "
            "  change_type, before_path, after_path, before_text, after_text, "
            "  alignment_confidence, detected_at, effective_date"
            ") VALUES ("
            "  :d, :v, :j, :s, "
            "  :ct, :bp, :ap, :bt, :at, "
            "  :conf, :now, :now"
            ")"
        ),
        {
            "d": document_id,
            "v": document_version_id,
            "j": jurisdiction,
            "s": sector,
            "ct": change_type,
            "bp": before_path,
            "ap": after_path,
            "bt": before_text,
            "at": after_text,
            "conf": alignment_confidence,
            "now": now,
        },
    )


def _seed(conn: Connection) -> None:
    uk_user = _make_user(conn, UK_EMAIL, UK_PASSWORD)
    eu_user = _make_user(conn, EU_EMAIL, EU_PASSWORD)
    _subscribe(conn, uk_user, "UK", "BANKING")
    _subscribe(conn, eu_user, "EU", "BANKING")

    uk_doc, uk_v1, uk_v2 = _make_doc_with_versions(
        conn, UK_DOC_LID, "UK", "BANKING", "UK Banking Act (sample, e2e)"
    )
    eu_doc, eu_v1, eu_v2 = _make_doc_with_versions(
        conn, EU_DOC_LID, "EU", "BANKING", "EU Banking Directive (sample, e2e)"
    )

    # Both versions carry the same structural clauses; the v1/v2 difference
    # is on the changed leaf (PART_2/SECTION_12/(a) for UK,
    # ARTICLE_4/CLAUSE_4.2 for EU). The side-by-side viewer renders both
    # panes from these clause rows. Clause paths follow the parser's anchor
    # convention (PART_/SECTION_/(letter)) so the same string matches the
    # corresponding change_event path verbatim — that's what drives
    # ClauseOverlay's auto-scroll/highlight.
    uk_structure_head = [
        ("PART_1", "Part 1 of the UK Banking Act sample."),
        ("PART_1/SECTION_1", "Section 1: Preliminary provisions."),
        ("PART_2/SECTION_12", "Section 12: Capital requirements."),
    ]
    _insert_clauses(
        conn,
        uk_v1,
        uk_structure_head + [("PART_2/SECTION_12/(a)", UK_MODIFIED_BEFORE)],
    )
    _insert_clauses(
        conn,
        uk_v2,
        uk_structure_head + [("PART_2/SECTION_12/(a)", UK_MODIFIED_AFTER)],
    )

    eu_structure_head = [
        ("ARTICLE_1", "Article 1 of the EU Banking Directive sample."),
        ("ARTICLE_4", "Article 4: Liquidity coverage."),
    ]
    _insert_clauses(
        conn,
        eu_v1,
        eu_structure_head + [("ARTICLE_4/CLAUSE_4.2", EU_MODIFIED_BEFORE)],
    )
    _insert_clauses(
        conn,
        eu_v2,
        eu_structure_head + [("ARTICLE_4/CLAUSE_4.2", EU_MODIFIED_AFTER)],
    )

    # 1. UK MODIFIED — primary assertion (green badge, "0.92"). Visible
    # to UK client only. before_path == after_path == the leaf clause's
    # canonical anchor so the ?before=&after= URL params trigger the
    # ClauseOverlay highlight in both panes.
    _emit_change_event(
        conn, uk_doc, uk_v2, "UK", "BANKING", "MODIFIED",
        "PART_2/SECTION_12/(a)", "PART_2/SECTION_12/(a)",
        UK_MODIFIED_BEFORE, UK_MODIFIED_AFTER, 0.92,
    )

    # 2. EU MODIFIED — amber badge, "0.78". Visible to EU client only.
    # The asymmetric visibility is what proves subscription RLS at the
    # browser layer.
    _emit_change_event(
        conn, eu_doc, eu_v2, "EU", "BANKING", "MODIFIED",
        "ARTICLE_4/CLAUSE_4.2", "ARTICLE_4/CLAUSE_4.2",
        EU_MODIFIED_BEFORE, EU_MODIFIED_AFTER, 0.78,
    )

    # 3. UK MOVED — suppressed by default in the /changes UI via the
    # ``Show MOVED`` toggle. The test asserts it is NOT visible to UK
    # without flipping the toggle.
    _emit_change_event(
        conn, uk_doc, uk_v2, "UK", "BANKING", "MOVED",
        "PART_3/SECTION_14", "PART_4/SECTION_14",
        UK_MOVED_TEXT, UK_MOVED_TEXT, 0.95,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed or tear down Playwright e2e fixtures (WU8.2).",
    )
    parser.add_argument(
        "--teardown",
        action="store_true",
        help="Remove fixtures and exit without re-seeding.",
    )
    args = parser.parse_args()

    raw = os.environ.get("HORIZONS_DB_URL")
    if not raw:
        print("HORIZONS_DB_URL is required", file=sys.stderr)
        return 1
    url = _normalise_db_url(raw)

    engine = create_engine(url, future=True)
    try:
        # Teardown and seed run in separate transactions on purpose: the
        # ``SET LOCAL session_replication_role = 'replica'`` in
        # ``_teardown`` is transaction-scoped, and we don't want that
        # trigger bypass to silently cover seed-side INSERTs as the
        # schema acquires more invariants over time.
        with engine.begin() as conn:
            _teardown(conn)
        if args.teardown:
            print("e2e fixtures removed.")
            return 0
        with engine.begin() as conn:
            _seed(conn)
        print(
            f"e2e fixtures seeded: {UK_EMAIL} (UK/BANKING), "
            f"{EU_EMAIL} (EU/BANKING)."
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

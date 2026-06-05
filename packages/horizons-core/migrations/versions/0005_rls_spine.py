"""Wire the RLS spine.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-05

This migration is the WU1.4 deliverable. It:

1. Creates the ``watchlists`` private-state table — the canonical
   user-owned, mutable, cross-client-privacy shape that future
   private-state aggregates (saved_queries, alerts) will mirror.
2. Enables ``ROW LEVEL SECURITY`` and ``FORCE ROW LEVEL SECURITY`` on
   ``watchlists`` plus the three corpus tables. ``FORCE`` makes the
   table owner (``schema_owner``) subject to policies too — defence in
   depth against a careless migration that runs an ad-hoc query under
   the owner role. ``admin_bypass`` (BYPASSRLS) is the only escape
   hatch.
3. Attaches the two-axis policy set:
     - Cross-client privacy (watchlists): owner-keyed, four policies
       (SELECT/INSERT/UPDATE/DELETE) all on ``TO api_app``.
     - Subscription scope (corpus): ``EXISTS(... current_scope() ...)``
       on ``TO api_app``. Child tables (document_versions, clauses)
       walk up to ``documents`` via FK — RLS does not transitively
       apply across tables.
     - Ingestion pass-through (corpus): explicit
       ``FOR ALL TO ingestion_worker USING (true) WITH CHECK (true)``
       so the worker keeps writing without scope filtering. Without
       this, RLS-on + no-applicable-policy = deny-by-default and the
       worker would see zero rows.

See ``horizons_core/db/rls.md`` for the architecture spec,
``db/schema.md`` for the ``watchlists`` aggregate description, and
``db/roles.md`` for the per-table grant matrix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Policy names live in one place so upgrade() and downgrade() agree.
_WATCHLISTS_POLICIES = (
    "watchlists_owner_select",
    "watchlists_owner_insert",
    "watchlists_owner_update",
    "watchlists_owner_delete",
)
_CORPUS_TABLES = ("documents", "document_versions", "clauses")
_CORPUS_API_POLICIES = {
    "documents": "documents_in_scope",
    "document_versions": "document_versions_in_scope",
    "clauses": "clauses_in_scope",
}
_CORPUS_INGESTION_POLICIES = {
    "documents": "documents_ingestion_all",
    "document_versions": "document_versions_ingestion_all",
    "clauses": "clauses_ingestion_all",
}


def upgrade() -> None:
    # ---- watchlists table ------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlists (
            id         uuid PRIMARY KEY DEFAULT uuidv7(),
            user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name       text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_watchlists_user_id
            ON watchlists (user_id);
        """
    )
    op.execute("ALTER TABLE watchlists OWNER TO schema_owner;")
    # api_app needs full CRUD on its own watchlists; RLS narrows the
    # effective surface to user_id = current_setting('app.user_id').
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON watchlists TO api_app;")
    # admin_bypass needs SELECT to be useful — BYPASSRLS bypasses RLS
    # but NOT table-level privileges. Reads only; mutations remain
    # disallowed.
    op.execute("GRANT SELECT ON watchlists TO admin_bypass;")
    op.execute(
        "COMMENT ON TABLE watchlists IS "
        "'Per-user saved query. Owner-keyed RLS (cross-client privacy axis). "
        "Mutable — rename and delete are real operations.';"
    )

    # ---- grant admin_bypass SELECT on corpus -----------------------------
    # Same rationale as watchlists: BYPASSRLS without GRANT is useless.
    # Reads only; ingestion remains the only writer.
    op.execute("GRANT SELECT ON documents TO admin_bypass;")
    op.execute("GRANT SELECT ON document_versions TO admin_bypass;")
    op.execute("GRANT SELECT ON clauses TO admin_bypass;")

    # ---- enable + FORCE RLS on all four tables ---------------------------
    # ENABLE alone leaves the table owner unfiltered. FORCE subjects the
    # owner to policies too. admin_bypass (BYPASSRLS) is unaffected.
    for tbl in ("watchlists", *_CORPUS_TABLES):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;")

    # ---- watchlists policies (cross-client privacy axis) -----------------
    # Each policy is dropped before create so re-runs are idempotent.
    for name in _WATCHLISTS_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {name} ON watchlists;")

    op.execute(
        """
        CREATE POLICY watchlists_owner_select ON watchlists
            FOR SELECT TO api_app
            USING (user_id = current_setting('app.user_id')::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY watchlists_owner_insert ON watchlists
            FOR INSERT TO api_app
            WITH CHECK (user_id = current_setting('app.user_id')::uuid);
        """
    )
    # UPDATE carries both USING and WITH CHECK so a row cannot be quietly
    # re-keyed to another user (USING filters which rows the UPDATE can
    # touch; WITH CHECK validates the resulting row).
    op.execute(
        """
        CREATE POLICY watchlists_owner_update ON watchlists
            FOR UPDATE TO api_app
            USING      (user_id = current_setting('app.user_id')::uuid)
            WITH CHECK (user_id = current_setting('app.user_id')::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY watchlists_owner_delete ON watchlists
            FOR DELETE TO api_app
            USING (user_id = current_setting('app.user_id')::uuid);
        """
    )

    # ---- corpus policies (subscription scope axis) -----------------------
    for tbl, name in _CORPUS_API_POLICIES.items():
        op.execute(f"DROP POLICY IF EXISTS {name} ON {tbl};")
    for tbl, name in _CORPUS_INGESTION_POLICIES.items():
        op.execute(f"DROP POLICY IF EXISTS {name} ON {tbl};")

    op.execute(
        """
        CREATE POLICY documents_in_scope ON documents
            FOR SELECT TO api_app
            USING (
                EXISTS (
                    SELECT 1 FROM app_private.current_scope() cs
                    WHERE cs.jurisdiction = documents.jurisdiction
                      AND cs.sector       = documents.sector
                )
            );
        """
    )
    # document_versions reaches scope via FK join through documents.
    # RLS predicates do not transitively apply across tables, so each
    # child table carries its own EXISTS walking up.
    op.execute(
        """
        CREATE POLICY document_versions_in_scope ON document_versions
            FOR SELECT TO api_app
            USING (
                EXISTS (
                    SELECT 1
                    FROM documents d
                    JOIN app_private.current_scope() cs
                      ON cs.jurisdiction = d.jurisdiction
                     AND cs.sector       = d.sector
                    WHERE d.id = document_versions.document_id
                )
            );
        """
    )
    op.execute(
        """
        CREATE POLICY clauses_in_scope ON clauses
            FOR SELECT TO api_app
            USING (
                EXISTS (
                    SELECT 1
                    FROM document_versions dv
                    JOIN documents d ON d.id = dv.document_id
                    JOIN app_private.current_scope() cs
                      ON cs.jurisdiction = d.jurisdiction
                     AND cs.sector       = d.sector
                    WHERE dv.id = clauses.document_version_id
                )
            );
        """
    )

    # Ingestion pass-through. Once RLS is enabled, a role with no
    # applicable policy is denied by default. The worker needs an
    # explicit allow-all policy so its writes succeed and its alignment
    # reads see everything it has written. The append-only triggers
    # from WU1.2 still reject UPDATE; the effective surface remains
    # SELECT + INSERT.
    op.execute(
        """
        CREATE POLICY documents_ingestion_all ON documents
            FOR ALL TO ingestion_worker
            USING (true) WITH CHECK (true);
        """
    )
    op.execute(
        """
        CREATE POLICY document_versions_ingestion_all ON document_versions
            FOR ALL TO ingestion_worker
            USING (true) WITH CHECK (true);
        """
    )
    op.execute(
        """
        CREATE POLICY clauses_ingestion_all ON clauses
            FOR ALL TO ingestion_worker
            USING (true) WITH CHECK (true);
        """
    )


def downgrade() -> None:
    for tbl, name in _CORPUS_INGESTION_POLICIES.items():
        op.execute(f"DROP POLICY IF EXISTS {name} ON {tbl};")
    for tbl, name in _CORPUS_API_POLICIES.items():
        op.execute(f"DROP POLICY IF EXISTS {name} ON {tbl};")
    for name in _WATCHLISTS_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {name} ON watchlists;")

    for tbl in ("watchlists", *_CORPUS_TABLES):
        op.execute(f"ALTER TABLE {tbl} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;")

    # Revoke the admin_bypass grants added by upgrade(). watchlists
    # grants disappear with the table; the corpus grants need explicit
    # revoke.
    for tbl in _CORPUS_TABLES:
        op.execute(f"REVOKE SELECT ON {tbl} FROM admin_bypass;")

    op.execute("DROP INDEX IF EXISTS idx_watchlists_user_id;")
    op.execute("DROP TABLE IF EXISTS watchlists;")

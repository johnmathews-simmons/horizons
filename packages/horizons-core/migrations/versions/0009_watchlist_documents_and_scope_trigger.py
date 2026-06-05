"""Wire watchlists to documents and enforce subscription scope.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-05

WU4.3 reshapes ``watchlists`` from a free-form "saved query" surface to a
"watched document" surface — each row links a user to one document the
user is watching. The change has three pieces:

1. **Schema** — add ``document_id uuid NOT NULL REFERENCES documents(id)``
   with ``ON DELETE CASCADE`` (if a document is removed, the watches go
   with it). Add an index on ``document_id`` to support reverse lookups
   ("who watches X?") for future alerting code paths.

2. **Trigger** — ``app_private.assert_watchlist_in_scope`` on
   ``BEFORE INSERT OR UPDATE OF document_id`` rejects any row whose
   document is outside the caller's current subscription scope. The
   trigger is **defence-in-depth** for the service-layer check in
   ``horizons_api.routes.watchlists`` — the service layer maps a scope
   violation to a clean ``422``, but a direct repository-bypassing
   write would still be stopped here.

   Design note: the trigger short-circuits silently when
   ``app.user_id`` is unset (admin / migration paths). It does NOT
   short-circuit by role, so the trigger fires under both ``api_app``
   *and* ``schema_owner`` once a GUC is present — this matches the WU1.4
   posture (``FORCE ROW LEVEL SECURITY`` applies to the table owner
   too) and means existing migration tests need the GUC if they want to
   exercise the trigger.

3. **RLS extension** — the WU1.4 watchlist RLS policies already enforce
   the cross-client privacy axis (``user_id`` equality). The scope axis
   is the trigger's job; we deliberately keep them as two layers rather
   than merging them into a single ``WITH CHECK`` so that scope errors
   come back as ``check_violation`` (mappable to 422) and ownership
   errors come back as RLS-level "row doesn't exist" (mappable to 404 /
   silently filtered). See ``db/rls.md`` for the two-axis model.

The existing watchlist policies (``watchlists_owner_*``) are unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- column + index --------------------------------------------------
    # The column lands NOT NULL, no default. The table is brand-new across
    # the demo deployment (no production data); pre-existing watchlist rows
    # in test fixtures are updated alongside this WU. If we later need to
    # apply this against a populated table, the two-step migration
    # (nullable → backfill → set NOT NULL) lives in the deployment runbook;
    # we don't carry that complexity here.
    op.execute(
        """
        ALTER TABLE watchlists
            ADD COLUMN document_id uuid NOT NULL
                REFERENCES documents(id) ON DELETE CASCADE;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_watchlists_document_id
            ON watchlists (document_id);
        """
    )

    # A user shouldn't be able to watch the same document twice — the
    # rename / annotate use case lands later as a separate column. Pair
    # (user_id, document_id) is the natural uniqueness.
    op.execute(
        """
        ALTER TABLE watchlists
            ADD CONSTRAINT watchlists_user_document_unique
                UNIQUE (user_id, document_id);
        """
    )

    # ---- schema usage / current_scope EXECUTE for admin_bypass -----------
    # The trigger function below is owned by admin_bypass (see comment
    # there for why). For ``CREATE`` / ``ALTER FUNCTION`` to succeed
    # against an admin_bypass-owned function in app_private, the role
    # needs USAGE on the schema. WU1.4 granted USAGE to ``api_app``
    # only; extending here keeps the schema's surface tight (still no
    # PUBLIC USAGE). The trigger also calls ``current_scope()`` (also
    # in app_private) — grant EXECUTE so the trigger's owner can
    # invoke it; WU1.4 granted EXECUTE to api_app only.
    op.execute("GRANT USAGE ON SCHEMA app_private TO admin_bypass;")
    op.execute("GRANT EXECUTE ON FUNCTION app_private.current_scope() TO admin_bypass;")

    # ---- trigger function ------------------------------------------------
    # SECURITY DEFINER + empty search_path mirrors current_scope(); we run
    # under schema_owner's privileges so the function can read documents
    # and call app_private.current_scope() without inheriting the caller's
    # role.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_private.assert_watchlist_in_scope()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = ''
        AS $$
        DECLARE
            uid_text text;
        BEGIN
            uid_text := pg_catalog.current_setting('app.user_id', true);
            -- Migration / admin paths run with app.user_id unset; the
            -- trigger has no scope to check against and yields to the
            -- caller's discipline (admin_bypass, schema_owner). Real API
            -- writes always have the GUC bound (the session bracket
            -- sets it before any write reaches this row).
            IF uid_text IS NULL OR uid_text = '' THEN
                RETURN NEW;
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM public.documents d
                JOIN app_private.current_scope() cs
                  ON cs.jurisdiction = d.jurisdiction
                 AND cs.sector       = d.sector
                WHERE d.id = NEW.document_id
            ) THEN
                RAISE EXCEPTION 'watchlist document % is outside subscription scope',
                    NEW.document_id
                    USING ERRCODE = 'check_violation';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    # OWNER TO admin_bypass — the function must read ``documents``,
    # which is under RLS FORCE with policies only on ``api_app`` and
    # ``ingestion_worker``. Under ``schema_owner`` the EXISTS query
    # would see zero rows by default (FORCE + no applicable policy ==
    # deny). ``admin_bypass`` has ``BYPASSRLS`` plus a static
    # ``SELECT ON documents`` GRANT (granted in WU1.4), which makes
    # this the minimal owner that lets the trigger compare the actual
    # document scope. The function's logic stays read-only and keyed
    # off the caller's ``app.user_id``, so the broader BYPASSRLS does
    # not leak: ``current_scope()`` already restricts to the caller's
    # subscriptions and the EXISTS only checks the specific
    # ``NEW.document_id``.
    op.execute("ALTER FUNCTION app_private.assert_watchlist_in_scope() OWNER TO admin_bypass;")
    op.execute("REVOKE ALL ON FUNCTION app_private.assert_watchlist_in_scope() FROM PUBLIC;")
    op.execute("GRANT EXECUTE ON FUNCTION app_private.assert_watchlist_in_scope() TO api_app;")

    # The trigger fires on INSERT and on UPDATE OF document_id only.
    # Renames / other column updates don't re-validate scope (the row was
    # validated at insertion and the document_id hasn't moved).
    op.execute(
        """
        CREATE TRIGGER watchlists_in_subscription_scope
            BEFORE INSERT OR UPDATE OF document_id ON watchlists
            FOR EACH ROW
            EXECUTE FUNCTION app_private.assert_watchlist_in_scope();
        """
    )

    op.execute(
        "COMMENT ON FUNCTION app_private.assert_watchlist_in_scope() IS "
        "'Defence-in-depth: rejects watchlist rows whose document is "
        "outside the caller''s current subscription scope. Short-circuits "
        "silently when app.user_id is unset (admin / migration paths).';"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS watchlists_in_subscription_scope ON watchlists;")
    op.execute("DROP FUNCTION IF EXISTS app_private.assert_watchlist_in_scope();")
    op.execute("REVOKE EXECUTE ON FUNCTION app_private.current_scope() FROM admin_bypass;")
    op.execute("REVOKE USAGE ON SCHEMA app_private FROM admin_bypass;")
    op.execute("ALTER TABLE watchlists DROP CONSTRAINT IF EXISTS watchlists_user_document_unique;")
    op.execute("DROP INDEX IF EXISTS idx_watchlists_document_id;")
    op.execute("ALTER TABLE watchlists DROP COLUMN IF EXISTS document_id;")

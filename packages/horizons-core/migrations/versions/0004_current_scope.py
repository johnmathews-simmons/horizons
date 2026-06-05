"""Create the ``app_private`` schema and ``current_scope()`` helper.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05

The ``app_private`` schema holds SECURITY DEFINER helpers that the
upcoming RLS policies will invoke. Today it contains exactly one
function: ``app_private.current_scope()``. It reads the session GUC
``app.user_id``, looks up the calling user's *currently active*
subscriptions, and returns the ``(jurisdiction, sector)`` pairs they
are entitled to read.

See ``horizons_core/db/rls.md`` for the full RLS architecture spec and
``horizons_core/db/schema.md`` for the table definitions this function
joins through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Schema. Owned by schema_owner; PUBLIC has no USAGE. api_app gets
    # USAGE so it can reach the function it's about to be granted
    # EXECUTE on. ingestion_worker and admin_bypass have no business
    # inside app_private.
    op.execute("CREATE SCHEMA IF NOT EXISTS app_private AUTHORIZATION schema_owner;")
    op.execute("REVOKE ALL ON SCHEMA app_private FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA app_private TO api_app;")

    # current_scope():
    #   - SECURITY DEFINER so it runs with schema_owner's privileges and
    #     can read public.subscriptions / public.subscription_scopes
    #     even when called by api_app.
    #   - SET search_path = '' is mandatory for SECURITY DEFINER: a
    #     malicious search_path could otherwise redirect the joined
    #     tables to attacker-controlled relations. Every identifier
    #     inside the body is schema-qualified to compensate.
    #   - STABLE: same inputs (the GUC + clock) produce the same output
    #     within a single statement; lets the planner cache the call.
    #   - Raises when app.user_id is unset. A silently-empty result set
    #     looks like "no data" to RLS-protected callers and hides bugs
    #     in the repository layer's SET LOCAL discipline.
    #   - DISTINCT on the SELECT: scope is set-semantics; overlapping
    #     subscriptions covering the same (jurisdiction, sector) collapse
    #     to one row.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_private.current_scope()
        RETURNS TABLE(jurisdiction text, sector text)
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = ''
        AS $$
        DECLARE
            uid_text text;
            uid      uuid;
        BEGIN
            uid_text := pg_catalog.current_setting('app.user_id', true);
            IF uid_text IS NULL OR uid_text = '' THEN
                RAISE EXCEPTION 'app.user_id is required';
            END IF;
            uid := uid_text::uuid;

            RETURN QUERY
            SELECT DISTINCT ss.jurisdiction, ss.sector
            FROM public.subscription_scopes ss
            JOIN public.subscriptions s ON s.id = ss.subscription_id
            WHERE s.user_id = uid
              AND s.valid_from <= pg_catalog.now()
              AND (s.valid_to IS NULL OR s.valid_to > pg_catalog.now());
        END;
        $$;
        """
    )

    op.execute("ALTER FUNCTION app_private.current_scope() OWNER TO schema_owner;")
    op.execute("REVOKE ALL ON FUNCTION app_private.current_scope() FROM PUBLIC;")
    op.execute("GRANT EXECUTE ON FUNCTION app_private.current_scope() TO api_app;")

    op.execute(
        "COMMENT ON FUNCTION app_private.current_scope() IS "
        "'Set of (jurisdiction, sector) the current request''s user is "
        "entitled to read. Reads app.user_id GUC; raises if unset. "
        "SECURITY DEFINER + empty search_path. Invoked by corpus-scope "
        "RLS policies. See horizons_core/db/rls.md.';"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_private.current_scope();")
    op.execute("DROP SCHEMA IF EXISTS app_private;")

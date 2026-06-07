"""Add app_public.change_event_shape() — SECURITY DEFINER change-matrix view.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-07

The home dashboard wants a per-(jurisdiction, sector) count of recorded
change events alongside the document count, so cards for jurisdictions
with zero observed changes can be flagged or routed away from the empty
``/changes`` page. The aggregate is corpus-shape data, same posture as
``corpus_shape()`` — non-sensitive, identical for every caller — so we
follow the same SECURITY DEFINER pattern rather than escalating to
``admin_bypass`` per page load.

Mirrors 0013: schema_owner-owned function, EXECUTE granted to
``api_app`` and ``admin_bypass``, plus a permissive schema_owner read
policy on ``change_events`` so FORCE ROW LEVEL SECURITY does not zero
out the SECURITY DEFINER body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_public.change_event_shape()
        RETURNS TABLE (
            jurisdiction text,
            sector text,
            change_count bigint
        )
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        SET search_path = ''
        AS $$
            SELECT jurisdiction, sector, COUNT(*)::bigint
            FROM public.change_events
            GROUP BY jurisdiction, sector;
        $$;
        """
    )

    op.execute("ALTER FUNCTION app_public.change_event_shape() OWNER TO schema_owner;")
    op.execute("REVOKE ALL ON FUNCTION app_public.change_event_shape() FROM PUBLIC;")
    op.execute("GRANT EXECUTE ON FUNCTION app_public.change_event_shape() TO api_app;")
    op.execute("GRANT EXECUTE ON FUNCTION app_public.change_event_shape() TO admin_bypass;")

    op.execute(
        """
        CREATE POLICY change_events_schema_owner_read ON change_events
            FOR SELECT TO schema_owner
            USING (true);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS change_events_schema_owner_read ON change_events;")
    op.execute("DROP FUNCTION IF EXISTS app_public.change_event_shape();")

"""Create the four-role security model.

Revision ID: 0001
Revises:
Create Date: 2026-06-05

Establishes the Postgres role taxonomy that the rest of the database
layer assumes. See ``horizons_core/db/roles.md`` for the design
rationale, GRANT shape, and how application services connect through
these roles.

All four roles are NOLOGIN — they are permission containers, not
connectable accounts. Per-environment LOGIN users are provisioned by
ops/IaC and granted the appropriate role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'schema_owner') THEN
                CREATE ROLE schema_owner NOLOGIN NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'api_app') THEN
                CREATE ROLE api_app NOLOGIN NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ingestion_worker') THEN
                CREATE ROLE ingestion_worker NOLOGIN NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin_bypass') THEN
                CREATE ROLE admin_bypass NOLOGIN BYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
        END
        $$;
        """
    )

    op.execute(
        "COMMENT ON ROLE schema_owner IS "
        "'Owns DDL objects (tables, indexes, sequences). Used only by migrations.';"
    )
    op.execute(
        "COMMENT ON ROLE api_app IS "
        "'Public API service role. NOBYPASSRLS — sees only its tenant''s rows "
        "via app.user_id session GUC.';"
    )
    op.execute(
        "COMMENT ON ROLE ingestion_worker IS "
        "'Ingestion worker role. NOBYPASSRLS — writes corpus rows but cannot read "
        "client-private state.';"
    )
    op.execute(
        "COMMENT ON ROLE admin_bypass IS "
        "'Audited admin escape hatch. BYPASSRLS — used only through "
        "explicit, logged admin endpoints.';"
    )


def downgrade() -> None:
    op.execute("DROP ROLE IF EXISTS admin_bypass;")
    op.execute("DROP ROLE IF EXISTS ingestion_worker;")
    op.execute("DROP ROLE IF EXISTS api_app;")
    op.execute("DROP ROLE IF EXISTS schema_owner;")

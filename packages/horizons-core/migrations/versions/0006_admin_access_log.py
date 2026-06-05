"""Audit table for admin operator and impersonation sessions.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-05

This migration is the WU1.9 deliverable. It adds ``admin_access_log`` —
the append-only audit table every admin code path writes to as the audit
trail for cross-tenant access (operator, BYPASSRLS) and impersonation
(api_app under a target user's ``app.user_id`` with the admin's id
captured in ``app.impersonating_admin_id``).

The shape mirrors WU1.1 / WU1.2 append-only patterns:

- Append-only via trigger (UPDATE / DELETE rejected outright).
- ``ROW LEVEL SECURITY`` enabled and FORCEd for defence in depth, but
  no policy is added because only ``admin_bypass`` (BYPASSRLS) writes
  here and BYPASSRLS sidesteps the lack-of-policy-deny-by-default.
- Per-role grants strictly tied to the audit purpose: ``schema_owner``
  for migrations; ``admin_bypass`` SELECT + INSERT; ``api_app`` and
  ``ingestion_worker`` get nothing.

See ``horizons_core/db/rls.md`` §Admin code paths and §Audit log table
for the architecture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = 'admin_access_mode'
            ) THEN
                CREATE TYPE admin_access_mode AS ENUM ('operator', 'impersonation');
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_access_log (
            id              uuid PRIMARY KEY DEFAULT uuidv7(),
            admin_id        uuid NOT NULL
                              REFERENCES users(id) ON DELETE RESTRICT,
            target_user_id  uuid
                              REFERENCES users(id) ON DELETE RESTRICT,
            mode            admin_access_mode NOT NULL,
            token_id        uuid,
            reason          text,
            granted_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT admin_access_log_mode_target_consistent CHECK (
                (mode = 'operator' AND target_user_id IS NULL)
                OR (mode = 'impersonation' AND target_user_id IS NOT NULL)
            )
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_admin_access_log_admin_id_granted_at
            ON admin_access_log (admin_id, granted_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_admin_access_log_target_user_id_granted_at
            ON admin_access_log (target_user_id, granted_at DESC)
            WHERE target_user_id IS NOT NULL;
        """
    )

    # Append-only: every UPDATE and DELETE is rejected outright. Same
    # shape as subscription_scopes' reject-on-update trigger (WU1.1).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_admin_access_log_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'admin_access_log is append-only; % not permitted', TG_OP;
        END;
        $$;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS admin_access_log_no_update ON admin_access_log;
        CREATE TRIGGER admin_access_log_no_update
            BEFORE UPDATE ON admin_access_log
            FOR EACH ROW EXECUTE FUNCTION reject_admin_access_log_mutation();
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS admin_access_log_no_delete ON admin_access_log;
        CREATE TRIGGER admin_access_log_no_delete
            BEFORE DELETE ON admin_access_log
            FOR EACH ROW EXECUTE FUNCTION reject_admin_access_log_mutation();
        """
    )

    op.execute("ALTER TYPE admin_access_mode OWNER TO schema_owner;")
    op.execute("ALTER TABLE admin_access_log OWNER TO schema_owner;")
    op.execute("ALTER FUNCTION reject_admin_access_log_mutation() OWNER TO schema_owner;")

    # Defence in depth: RLS on with FORCE so even schema_owner (which
    # would otherwise bypass policies because it owns the table) is
    # subject to whatever rule we add later. No policy is added today
    # because only admin_bypass writes here and BYPASSRLS sidesteps the
    # default-deny. If a future read path under api_app needs visibility
    # (e.g. an admin viewing their own audit trail), the policy will be
    # added in that work unit's migration.
    op.execute("ALTER TABLE admin_access_log ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE admin_access_log FORCE ROW LEVEL SECURITY;")

    # Grants: admin_bypass writes (and reads back for the integration
    # test's verification step). api_app and ingestion_worker are not
    # touched — the table is invisible to them.
    op.execute("GRANT SELECT, INSERT ON admin_access_log TO admin_bypass;")
    op.execute("GRANT USAGE ON TYPE admin_access_mode TO admin_bypass;")

    op.execute(
        "COMMENT ON TABLE admin_access_log IS "
        "'Append-only audit row per admin operator or impersonation session "
        "(WU1.9). One row written by core.auth.admin context managers on "
        "entry. token_id reserved for Track-4 JWT id binding.';"
    )
    op.execute(
        "COMMENT ON TYPE admin_access_mode IS "
        "'Two-value ENUM: operator = admin_bypass cross-tenant read; "
        "impersonation = api_app under a target user''s app.user_id.';"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS admin_access_log_no_delete ON admin_access_log;")
    op.execute("DROP TRIGGER IF EXISTS admin_access_log_no_update ON admin_access_log;")
    op.execute("DROP FUNCTION IF EXISTS reject_admin_access_log_mutation();")
    op.execute("DROP INDEX IF EXISTS idx_admin_access_log_target_user_id_granted_at;")
    op.execute("DROP INDEX IF EXISTS idx_admin_access_log_admin_id_granted_at;")
    op.execute("DROP TABLE IF EXISTS admin_access_log;")
    op.execute("DROP TYPE IF EXISTS admin_access_mode;")

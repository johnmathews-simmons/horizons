"""Refresh-token registry for explicit revocation.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-05

WU4.0 deliverable. Access tokens are short-lived and not revocable
individually; refresh tokens are long-lived and need a server-side
revocation surface so a logout / compromise can drop them out of
circulation immediately.

One row per refresh token; the primary key is the JWT's ``jti``.
``revoked_at`` is null until the row is retired. RLS is owner-only
under ``app.user_id`` — the same shape as ``watchlists`` — because the
refresh-flow endpoint (WU4.2) decodes the bearer first, binds the
session to ``sub``, then queries by ``jti`` under RLS. ``admin_bypass``
gets SELECT for support tooling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            jti           uuid PRIMARY KEY,
            user_id       uuid NOT NULL
                            REFERENCES users(id) ON DELETE CASCADE,
            issued_at     timestamptz NOT NULL DEFAULT now(),
            expires_at    timestamptz NOT NULL,
            revoked_at    timestamptz
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id_issued_at
            ON refresh_tokens (user_id, issued_at DESC);
        """
    )

    op.execute("ALTER TABLE refresh_tokens OWNER TO schema_owner;")

    # RLS owner-only — same predicate shape as watchlists. The refresh
    # endpoint binds app.user_id from the decoded sub before looking up
    # the jti, so the policy fires for the legitimate owner.
    op.execute("ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE refresh_tokens FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS refresh_tokens_owner_select ON refresh_tokens;")
    op.execute(
        """
        CREATE POLICY refresh_tokens_owner_select ON refresh_tokens
            FOR SELECT TO api_app
            USING (user_id = current_setting('app.user_id')::uuid);
        """
    )
    op.execute("DROP POLICY IF EXISTS refresh_tokens_owner_insert ON refresh_tokens;")
    op.execute(
        """
        CREATE POLICY refresh_tokens_owner_insert ON refresh_tokens
            FOR INSERT TO api_app
            WITH CHECK (user_id = current_setting('app.user_id')::uuid);
        """
    )
    op.execute("DROP POLICY IF EXISTS refresh_tokens_owner_update ON refresh_tokens;")
    op.execute(
        """
        CREATE POLICY refresh_tokens_owner_update ON refresh_tokens
            FOR UPDATE TO api_app
            USING (user_id = current_setting('app.user_id')::uuid)
            WITH CHECK (user_id = current_setting('app.user_id')::uuid);
        """
    )

    # Grants. api_app reads, inserts, and marks rows revoked (UPDATE on
    # revoked_at). No DELETE — retired rows stay on disk as the audit
    # trail; a separate housekeeping job will prune by expires_at.
    op.execute("GRANT SELECT, INSERT, UPDATE ON refresh_tokens TO api_app;")
    op.execute("GRANT SELECT ON refresh_tokens TO admin_bypass;")

    op.execute(
        "COMMENT ON TABLE refresh_tokens IS "
        "'Server-side refresh-token registry (WU4.0). One row per issued "
        "refresh token, keyed on JWT jti. revoked_at NULL = live; set = "
        "retired. Refresh endpoint queries by jti under owner-RLS.';"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS refresh_tokens_owner_update ON refresh_tokens;")
    op.execute("DROP POLICY IF EXISTS refresh_tokens_owner_insert ON refresh_tokens;")
    op.execute("DROP POLICY IF EXISTS refresh_tokens_owner_select ON refresh_tokens;")
    op.execute("DROP INDEX IF EXISTS idx_refresh_tokens_user_id_issued_at;")
    op.execute("DROP TABLE IF EXISTS refresh_tokens;")

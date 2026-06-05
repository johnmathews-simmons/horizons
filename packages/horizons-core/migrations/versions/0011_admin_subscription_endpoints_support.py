"""Schema support for admin subscription management.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-05

WU4.5 lands three Postgres-side primitives the admin subscription
endpoints rely on:

1. **``subscription_scopes.valid_to``** — a nullable ``timestamptz``
   column that gives scope rows the same "soft-delete" shape that
   ``subscriptions`` already has. Removing a (jurisdiction, sector) from
   a subscription is therefore append-only: the row stays for audit,
   only ``valid_to`` is set. The append-only trigger on the table
   relaxes from "reject every UPDATE" to "allow ``valid_to`` to move
   NULL → timestamp once, reject everything else", mirroring the
   ``subscriptions`` trigger from WU1.1.

2. **``app_private.current_scope()`` updated** to filter scope rows by
   ``valid_to`` (NULL or in the future), so a soft-deleted scope row
   stops contributing to a client's read surface immediately. The
   subscription-level ``valid_from`` / ``valid_to`` filter from WU1.3
   stays in place; the two windows compose.

3. **``watchlists.active``** — a ``boolean NOT NULL DEFAULT true``
   column. When an admin removes a scope, the route layer flips
   ``active`` to ``false`` on every watchlist whose document is no
   longer in the caller's reduced scope. Soft-hide, not delete: the row
   stays for audit and can be restored by adding the scope back. The
   ``WatchlistsRepository.list_for`` query (the client-facing read)
   filters ``active = true`` so soft-hidden rows disappear from the
   user-visible surface; ``admin_bypass`` sessions see every row.

   Also grants ``admin_bypass`` ``UPDATE ON watchlists`` so the soft-hide
   actually lands — WU1.4 granted only SELECT.

Defence-in-depth: the route layer is the primary writer for both axes,
the database constraints / trigger / RLS are the second layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- subscription_scopes.valid_to + relaxed update trigger ---------
    op.execute(
        """
        ALTER TABLE subscription_scopes
            ADD COLUMN valid_to timestamptz NULL;
        """
    )

    # Same pattern as ``reject_subscription_update`` (WU1.1): the only
    # permitted UPDATE is moving ``valid_to`` from NULL to a non-NULL
    # timestamp, with every other column unchanged. Everything else
    # raises.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_subscription_scope_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.valid_to IS NULL
               AND NEW.valid_to IS NOT NULL
               AND NEW.subscription_id = OLD.subscription_id
               AND NEW.jurisdiction    = OLD.jurisdiction
               AND NEW.sector          = OLD.sector THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'subscription_scopes is append-only except for ending '
                'valid_to (NULL -> timestamp), got UPDATE attempting to '
                'change other columns';
        END;
        $$;
        """
    )

    # ---- current_scope() filters by valid_to too -----------------------
    # The previous version (WU1.3) joined every subscription_scopes row
    # for the user's active subscriptions. Now scope rows themselves have
    # a soft-delete window, so the predicate composes the two.
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
              AND (s.valid_to  IS NULL OR s.valid_to  > pg_catalog.now())
              AND (ss.valid_to IS NULL OR ss.valid_to > pg_catalog.now());
        END;
        $$;
        """
    )

    # ---- watchlists.active --------------------------------------------
    # NOT NULL with default true means existing rows stay visible. The
    # client read path (WatchlistsRepository.list_for) filters
    # active=true; admin_bypass sees both active and soft-hidden rows.
    op.execute(
        """
        ALTER TABLE watchlists
            ADD COLUMN active boolean NOT NULL DEFAULT true;
        """
    )

    # admin_bypass needs UPDATE so the soft-hide path can flip ``active``
    # to false. WU1.4 granted only SELECT on watchlists; this extends to
    # what the WU4.5 admin reduction path needs without touching the
    # client-facing api_app grants.
    op.execute("GRANT UPDATE ON watchlists TO admin_bypass;")

    # admin_bypass tenancy-table grants for the WU4.5 admin endpoints.
    # The WU1.1 schema migration granted these tables to ``api_app``
    # only; the WU1.9 admin context managers never touched the tenancy
    # ledger because no admin path needed it. WU4.5's
    # /v1/admin/subscriptions changes that:
    #
    # - SELECT on ``users`` so the admin can verify a target user
    #   exists before creating or modifying their subscription.
    # - SELECT + INSERT on ``subscriptions`` so the admin POST can
    #   write a new subscription row.
    # - SELECT + INSERT on ``subscription_scopes`` so the admin POST
    #   and PATCH can append scope rows.
    # - UPDATE on ``subscription_scopes`` so the admin PATCH can
    #   soft-delete a scope row (NULL -> timestamp; trigger-policed).
    #
    # We deliberately do NOT grant UPDATE on ``subscriptions`` here.
    # Ending a subscription would happen via a future endpoint that
    # also writes a replacement; PATCH only mutates scope rows.
    op.execute("GRANT SELECT ON users TO admin_bypass;")
    op.execute("GRANT SELECT, INSERT ON subscriptions TO admin_bypass;")
    op.execute("GRANT SELECT, INSERT, UPDATE ON subscription_scopes TO admin_bypass;")
    # Documents lookup for the soft-hide pass: ``soft_hide_out_of_scope``
    # joins documents to subscription_scopes. WU1.4 already granted
    # SELECT on documents to admin_bypass; the reduction path reuses
    # that grant.

    op.execute(
        "COMMENT ON COLUMN watchlists.active IS "
        "'False means the row is soft-hidden (e.g. subscription scope "
        "reduced; document no longer in the client''s scope). Clients "
        "filter active=true; admin views see every row.';"
    )
    op.execute(
        "COMMENT ON COLUMN subscription_scopes.valid_to IS "
        "'NULL while the scope is active. UPDATE NULL -> timestamp ends "
        "the scope row (soft-delete). UPDATE in any other shape rejected "
        "by trigger.';"
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON subscription_scopes FROM admin_bypass;")
    op.execute("REVOKE SELECT, INSERT ON subscriptions FROM admin_bypass;")
    op.execute("REVOKE SELECT ON users FROM admin_bypass;")
    op.execute("REVOKE UPDATE ON watchlists FROM admin_bypass;")
    op.execute("ALTER TABLE watchlists DROP COLUMN IF EXISTS active;")

    # Restore the original (reject-every-UPDATE) shape of the trigger.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_subscription_scope_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'subscription_scopes is append-only; UPDATE not permitted';
        END;
        $$;
        """
    )

    # Restore the WU1.3-shape current_scope() (no scope valid_to filter).
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

    op.execute("ALTER TABLE subscription_scopes DROP COLUMN IF EXISTS valid_to;")

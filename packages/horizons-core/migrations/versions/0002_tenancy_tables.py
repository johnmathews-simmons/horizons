"""Create the tenancy tables: users, subscriptions, subscription_scopes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-05

The three aggregates of the tenancy spine. ``users`` is the account
identity (mutable, no trigger). ``subscriptions`` and
``subscription_scopes`` are the audit/billing ledger and are
trigger-policed append-only: the only ``UPDATE`` the database
permits on ``subscriptions`` is moving ``valid_to`` from ``NULL`` to a
timestamp (i.e. ending a subscription); ``subscription_scopes`` rejects
``UPDATE`` outright.

See ``horizons_core/db/schema.md`` for the aggregate descriptions and
``horizons_core/db/roles.md`` for the per-table grants story.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Self-grant schema_owner — a safety net for the existing demo
    # deployment that ran 0001 before the self-grant was added there.
    # Idempotent: GRANT of an already-held role is a no-op. Fresh
    # deploys get this from 0001 (cleaner location); for already-
    # bootstrapped envs the line here lets `ALTER … OWNER TO
    # schema_owner` below succeed on re-run.
    op.execute("GRANT schema_owner TO current_user;")

    # PG 18 + Azure Flex strip PUBLIC's CREATE on the `public` schema.
    # `ALTER TYPE … OWNER TO schema_owner` requires the new owner to
    # have CREATE on the type's schema, which schema_owner doesn't
    # inherit by default. Grant it explicitly (idempotent).
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO schema_owner;")

    # ENUM type for users.role. Created idempotently so a partial-failure
    # re-run does not abort on duplicate type.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = 'user_role'
            ) THEN
                CREATE TYPE user_role AS ENUM ('client', 'admin');
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            uuid PRIMARY KEY DEFAULT uuidv7(),
            email         text NOT NULL UNIQUE,
            password_hash text NOT NULL,
            role          user_role NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id          uuid PRIMARY KEY DEFAULT uuidv7(),
            user_id     uuid NOT NULL
                          REFERENCES users(id) ON DELETE RESTRICT,
            valid_from  timestamptz NOT NULL,
            valid_to    timestamptz,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT subscriptions_valid_to_after_valid_from
                CHECK (valid_to IS NULL OR valid_to > valid_from)
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id_valid_from
            ON subscriptions (user_id, valid_from);
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS subscription_scopes (
            subscription_id uuid NOT NULL
                              REFERENCES subscriptions(id) ON DELETE CASCADE,
            jurisdiction    text NOT NULL,
            sector          text NOT NULL,
            PRIMARY KEY (subscription_id, jurisdiction, sector)
        );
        """
    )

    # Append-only triggers. subscriptions allows exactly one transition:
    # valid_to NULL -> non-NULL with every other column unchanged.
    # subscription_scopes rejects every UPDATE outright.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_subscription_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.valid_to IS NULL
               AND NEW.valid_to IS NOT NULL
               AND NEW.id         = OLD.id
               AND NEW.user_id    = OLD.user_id
               AND NEW.valid_from = OLD.valid_from
               AND NEW.created_at = OLD.created_at THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'subscriptions is append-only except for ending valid_to '
                '(NULL -> timestamp), got UPDATE attempting to change other columns';
        END;
        $$;
        """
    )

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

    op.execute(
        """
        DROP TRIGGER IF EXISTS subscriptions_no_update ON subscriptions;
        CREATE TRIGGER subscriptions_no_update
            BEFORE UPDATE ON subscriptions
            FOR EACH ROW EXECUTE FUNCTION reject_subscription_update();
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS subscription_scopes_no_update ON subscription_scopes;
        CREATE TRIGGER subscription_scopes_no_update
            BEFORE UPDATE ON subscription_scopes
            FOR EACH ROW EXECUTE FUNCTION reject_subscription_scope_update();
        """
    )

    # Ownership: schema_owner owns DDL.
    op.execute("ALTER TYPE user_role OWNER TO schema_owner;")
    op.execute("ALTER TABLE users OWNER TO schema_owner;")
    op.execute("ALTER TABLE subscriptions OWNER TO schema_owner;")
    op.execute("ALTER TABLE subscription_scopes OWNER TO schema_owner;")
    op.execute("ALTER FUNCTION reject_subscription_update() OWNER TO schema_owner;")
    op.execute("ALTER FUNCTION reject_subscription_scope_update() OWNER TO schema_owner;")

    # Grants: api_app reads + writes; ingestion_worker / admin_bypass none.
    # UPDATE on subscriptions is granted but trigger-policed; UPDATE on
    # subscription_scopes is granted then immediately rejected by the
    # trigger — an extra defence-in-depth layer would be REVOKE UPDATE,
    # but keeping the grant pattern uniform lets the trigger be the
    # single source of truth about *what* is forbidden.
    op.execute("GRANT SELECT, INSERT, UPDATE ON users TO api_app;")
    op.execute("GRANT SELECT, INSERT, UPDATE ON subscriptions TO api_app;")
    op.execute("GRANT SELECT, INSERT ON subscription_scopes TO api_app;")
    op.execute("GRANT USAGE ON TYPE user_role TO api_app;")

    # Self-documentation.
    op.execute(
        "COMMENT ON TABLE users IS "
        "'Account identity. Mutable (password / email change). "
        "Persists across cancel/resubscribe cycles.';"
    )
    op.execute(
        "COMMENT ON TABLE subscriptions IS "
        "'Time-bounded entitlements. Append-only via trigger: only "
        "permitted UPDATE is valid_to NULL -> timestamp.';"
    )
    op.execute(
        "COMMENT ON TABLE subscription_scopes IS "
        "'Jurisdiction x sector coverage per subscription. Append-only "
        "via trigger (UPDATE rejected outright).';"
    )
    op.execute(
        "COMMENT ON TYPE user_role IS "
        "'Two-value ENUM matching horizons_core.db.models.users.UserRole.';"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS subscription_scopes_no_update ON subscription_scopes;")
    op.execute("DROP TRIGGER IF EXISTS subscriptions_no_update ON subscriptions;")
    op.execute("DROP FUNCTION IF EXISTS reject_subscription_scope_update();")
    op.execute("DROP FUNCTION IF EXISTS reject_subscription_update();")
    op.execute("DROP TABLE IF EXISTS subscription_scopes;")
    op.execute("DROP TABLE IF EXISTS subscriptions;")
    op.execute("DROP TABLE IF EXISTS users;")
    op.execute("DROP TYPE IF EXISTS user_role;")

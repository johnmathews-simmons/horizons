"""Add app_public.corpus_shape() — SECURITY DEFINER corpus-matrix view.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-06

The WU8.5 home dashboard needs the corpus-wide (jurisdiction, sector,
document_count) matrix even when the caller is a scoped client who
cannot read the underlying ``documents`` rows. Corpus *shape* is
non-sensitive catalog data — clients already know the subscription
token vocabulary — so we expose an unscoped count via a SECURITY
DEFINER function rather than escalating to admin_bypass per request
(which would force an audit row for every page load).

The function is owned by ``postgres`` (the only role that can read
``documents`` unscoped), so SECURITY DEFINER inherits that read.
EXECUTE is granted to ``api_app``; ``admin_bypass`` already reads
the documents table directly.

``app_public`` schema is created here (first migration to use it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Schema. Owned by schema_owner; PUBLIC has no USAGE. api_app and
    # admin_bypass get USAGE so they can resolve the function they are
    # about to be granted EXECUTE on.
    op.execute("CREATE SCHEMA IF NOT EXISTS app_public AUTHORIZATION schema_owner;")
    op.execute("REVOKE ALL ON SCHEMA app_public FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA app_public TO api_app;")
    op.execute("GRANT USAGE ON SCHEMA app_public TO admin_bypass;")

    # corpus_shape():
    #   - SECURITY DEFINER so it runs with schema_owner's privileges and
    #     can read public.documents unscoped even when called by api_app.
    #   - SET search_path = '' is mandatory for SECURITY DEFINER: a
    #     malicious search_path could otherwise redirect the joined
    #     tables to attacker-controlled relations. Every identifier
    #     inside the body is schema-qualified to compensate.
    #   - STABLE: pure aggregate over an immutable snapshot; safe to
    #     cache within a statement.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_public.corpus_shape()
        RETURNS TABLE (
            jurisdiction text,
            sector text,
            document_count bigint
        )
        LANGUAGE sql
        SECURITY DEFINER
        STABLE
        SET search_path = ''
        AS $$
            SELECT jurisdiction, sector, COUNT(*)::bigint
            FROM public.documents
            GROUP BY jurisdiction, sector;
        $$;
        """
    )

    op.execute("ALTER FUNCTION app_public.corpus_shape() OWNER TO schema_owner;")
    op.execute("REVOKE ALL ON FUNCTION app_public.corpus_shape() FROM PUBLIC;")
    op.execute("GRANT EXECUTE ON FUNCTION app_public.corpus_shape() TO api_app;")
    op.execute("GRANT EXECUTE ON FUNCTION app_public.corpus_shape() TO admin_bypass;")

    # ``documents`` has FORCE ROW LEVEL SECURITY (migration 0005), so
    # ``schema_owner`` — despite owning the table — is subject to RLS
    # when SECURITY DEFINER runs the function body. The two existing
    # policies only cover ``api_app`` and ``ingestion_worker``; without a
    # policy for ``schema_owner`` the default is deny-by-default → 0 rows.
    # Add an unconditional read policy so the SECURITY DEFINER call-path
    # works as intended.
    op.execute(
        """
        CREATE POLICY documents_schema_owner_read ON documents
            FOR SELECT TO schema_owner
            USING (true);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS documents_schema_owner_read ON documents;")
    op.execute("DROP FUNCTION IF EXISTS app_public.corpus_shape();")
    op.execute("DROP SCHEMA IF EXISTS app_public;")

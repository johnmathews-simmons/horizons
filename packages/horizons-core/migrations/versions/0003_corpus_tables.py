"""Create the corpus tables: documents, document_versions, clauses.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-05

The three corpus aggregates. ``documents`` is the stable identity for a
legal text in the upstream Lawstronaut feed; ``document_versions`` are
the time-stamped re-issues of that document; ``clauses`` are the
heading-anchored fragments that hang off a particular version and carry
identity across versions via ``clause_uid``.

All three are append-only at the database layer: a trigger on each
table rejects every ``UPDATE``. New content is a new row. The
ingestion worker writes; the API reads. See
``horizons_core/db/schema.md`` for the aggregate descriptions and
``horizons_core/db/roles.md`` for the per-table grants story.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # documents — stable identity for an upstream legal text.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id                      uuid PRIMARY KEY DEFAULT uuidv7(),
            jurisdiction            text NOT NULL,
            sector                  text NOT NULL,
            lawstronaut_document_id text NOT NULL UNIQUE,
            title                   text NOT NULL,
            created_at              timestamptz NOT NULL DEFAULT now()
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_documents_jurisdiction_sector
            ON documents (jurisdiction, sector);
        """
    )

    # document_versions — time-stamped re-issues of a document.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_versions (
            id                     uuid PRIMARY KEY DEFAULT uuidv7(),
            document_id            uuid NOT NULL
                                     REFERENCES documents(id) ON DELETE RESTRICT,
            version_label          text NOT NULL,
            publication_date       timestamptz,
            effective_date         timestamptz,
            content_blob_container text NOT NULL,
            content_blob_key       text NOT NULL,
            content_sha256         bytea NOT NULL,
            content_bytes          int NOT NULL,
            created_at             timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT document_versions_unique_label
                UNIQUE (document_id, version_label),
            CONSTRAINT document_versions_content_bytes_nonneg
                CHECK (content_bytes >= 0),
            CONSTRAINT document_versions_sha256_length
                CHECK (octet_length(content_sha256) = 32)
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_versions_doc_effective
            ON document_versions (document_id, effective_date);
        """
    )

    # clauses — heading-anchored fragments of a particular version.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS clauses (
            id                  uuid PRIMARY KEY DEFAULT uuidv7(),
            document_version_id uuid NOT NULL
                                  REFERENCES document_versions(id) ON DELETE RESTRICT,
            clause_uid          uuid NOT NULL,
            clause_path         text NOT NULL,
            text_content        text NOT NULL,
            ord                 int  NOT NULL,
            CONSTRAINT clauses_unique_path_per_version
                UNIQUE (document_version_id, clause_path)
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_clauses_version_ord
            ON clauses (document_version_id, ord);
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_clauses_clause_uid
            ON clauses (clause_uid);
        """
    )

    # Append-only triggers. Each table rejects every UPDATE — the corpus
    # is immutable, mistakes are corrected by inserting a new version.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_document_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'documents is append-only; UPDATE not permitted '
                '(insert a new document_versions row to record a change)';
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_document_version_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'document_versions is append-only; UPDATE not permitted';
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_clause_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'clauses is append-only; UPDATE not permitted';
        END;
        $$;
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS documents_no_update ON documents;
        CREATE TRIGGER documents_no_update
            BEFORE UPDATE ON documents
            FOR EACH ROW EXECUTE FUNCTION reject_document_update();
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS document_versions_no_update ON document_versions;
        CREATE TRIGGER document_versions_no_update
            BEFORE UPDATE ON document_versions
            FOR EACH ROW EXECUTE FUNCTION reject_document_version_update();
        """
    )

    op.execute(
        """
        DROP TRIGGER IF EXISTS clauses_no_update ON clauses;
        CREATE TRIGGER clauses_no_update
            BEFORE UPDATE ON clauses
            FOR EACH ROW EXECUTE FUNCTION reject_clause_update();
        """
    )

    # Ownership: schema_owner owns DDL.
    op.execute("ALTER TABLE documents OWNER TO schema_owner;")
    op.execute("ALTER TABLE document_versions OWNER TO schema_owner;")
    op.execute("ALTER TABLE clauses OWNER TO schema_owner;")
    op.execute("ALTER FUNCTION reject_document_update() OWNER TO schema_owner;")
    op.execute("ALTER FUNCTION reject_document_version_update() OWNER TO schema_owner;")
    op.execute("ALTER FUNCTION reject_clause_update() OWNER TO schema_owner;")

    # Grants:
    #   api_app          — SELECT only (read-side of the public API).
    #   ingestion_worker — SELECT + INSERT (writes corpus rows; reads its
    #                      own work during alignment).
    #   admin_bypass     — no static grants; reaches in via SET LOCAL ROLE.
    op.execute("GRANT SELECT ON documents TO api_app;")
    op.execute("GRANT SELECT ON document_versions TO api_app;")
    op.execute("GRANT SELECT ON clauses TO api_app;")
    op.execute("GRANT SELECT, INSERT ON documents TO ingestion_worker;")
    op.execute("GRANT SELECT, INSERT ON document_versions TO ingestion_worker;")
    op.execute("GRANT SELECT, INSERT ON clauses TO ingestion_worker;")

    # Self-documentation.
    op.execute(
        "COMMENT ON TABLE documents IS "
        "'Stable identity for an upstream legal text. Append-only via "
        "trigger; lawstronaut_document_id is the upstream key.';"
    )
    op.execute(
        "COMMENT ON TABLE document_versions IS "
        "'Time-stamped re-issue of a document. Append-only via trigger. "
        "Content lives in blob storage at (container, key); the row keeps "
        "the sha256 and byte count for integrity.';"
    )
    op.execute(
        "COMMENT ON TABLE clauses IS "
        "'Heading-anchored fragment of a document_version. clause_uid "
        "carries identity across versions; clause_path is positional and "
        "renumbers freely. Append-only via trigger.';"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS clauses_no_update ON clauses;")
    op.execute("DROP TRIGGER IF EXISTS document_versions_no_update ON document_versions;")
    op.execute("DROP TRIGGER IF EXISTS documents_no_update ON documents;")
    op.execute("DROP FUNCTION IF EXISTS reject_clause_update();")
    op.execute("DROP FUNCTION IF EXISTS reject_document_version_update();")
    op.execute("DROP FUNCTION IF EXISTS reject_document_update();")
    op.execute("DROP TABLE IF EXISTS clauses;")
    op.execute("DROP TABLE IF EXISTS document_versions;")
    op.execute("DROP TABLE IF EXISTS documents;")

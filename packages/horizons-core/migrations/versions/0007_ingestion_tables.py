"""Add ingestion-side schema: poll schedule, incident log, version validity.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-05

WU3.1 — the ingestion worker's database surface. Three things:

1. Extends ``document_versions`` with the validity-window columns the
   per-document poll transaction (WU3.4) writes: ``version_no``,
   ``valid_from``, ``valid_to``. All nullable for now (expand-contract);
   WU3.4 will populate them and a later migration can tighten to
   ``NOT NULL`` once application code consistently fills them.
2. Narrows ``document_versions``'s append-only trigger so it permits
   ``UPDATE`` iff ``valid_to`` is the only column that changed — this
   is the path the design doc names as "extending the live version's
   ``valid_to``" on each unchanged poll. Every other UPDATE is still
   rejected; corpus rows remain otherwise immutable.
3. Adds ``document_poll_schedule`` and ``ingestion_incident``. The
   schedule table is the substrate for the SKIP LOCKED claim loop
   spec'd in ``docs/adrs/0001-worker-shape.md``. The incident table is
   an append-only log keyed by ``bigserial id`` (matches the
   ``change_events`` precedent).

Grants:
- ``ingestion_worker`` gets ``SELECT, INSERT, UPDATE`` on
  ``document_poll_schedule`` (claim loop bumps ``next_poll_at`` /
  ``last_polled_at`` / ``failure_count``).
- ``ingestion_worker`` gets ``SELECT, INSERT`` on ``ingestion_incident``
  (append-only — no UPDATE grant; no trigger needed because there is
  no UPDATE path the schema exposes).
- ``ingestion_worker`` gets ``UPDATE (valid_to)`` on
  ``document_versions`` — column-scoped to make the boundary explicit;
  the trigger is the substantive rule.
- ``api_app`` and ``admin_bypass`` get zero grants on the two new
  operator-only tables. They are unreachable from client code paths.

No RLS on the two new tables: the access pattern is operator-only,
``client`` (= ``api_app``) has no grants, and admin reads go through
the audited path that surfaces aggregate health stats — not raw rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Extend document_versions with the validity-window columns.
    op.execute(
        """
        ALTER TABLE document_versions
            ADD COLUMN IF NOT EXISTS version_no   int,
            ADD COLUMN IF NOT EXISTS valid_from   timestamptz,
            ADD COLUMN IF NOT EXISTS valid_to     timestamptz;
        """
    )

    op.execute(
        """
        ALTER TABLE document_versions
            ADD CONSTRAINT document_versions_unique_doc_version_no
                UNIQUE (document_id, version_no);
        """
    )

    # Live-version lookup: highest (document_id, valid_to) per document.
    # Index ordering is (document_id ASC, valid_to DESC) so the planner
    # can satisfy `WHERE document_id = ? ORDER BY valid_to DESC LIMIT 1`
    # from the index alone.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_versions_doc_valid_to
            ON document_versions (document_id, valid_to DESC);
        """
    )

    # 2. Narrow the append-only trigger to permit valid_to-only updates.
    #
    # The trigger is BEFORE UPDATE on document_versions. It raises iff
    # any column other than valid_to changed. valid_to may go from NULL
    # to a timestamp, from a timestamp to a later timestamp (the
    # design-doc "extend" path), or back to NULL — the table-level rule
    # is "valid_to is mutable; everything else is not". Monotonicity of
    # valid_to is enforced by the ingestion worker (WU3.4), not here,
    # mirroring the no-monotonic-guard decision documented for
    # subscriptions in horizons_core/db/schema.md.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_document_version_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id              IS DISTINCT FROM OLD.id
               OR NEW.document_id  IS DISTINCT FROM OLD.document_id
               OR NEW.version_label IS DISTINCT FROM OLD.version_label
               OR NEW.version_no   IS DISTINCT FROM OLD.version_no
               OR NEW.publication_date IS DISTINCT FROM OLD.publication_date
               OR NEW.effective_date IS DISTINCT FROM OLD.effective_date
               OR NEW.content_blob_container
                   IS DISTINCT FROM OLD.content_blob_container
               OR NEW.content_blob_key IS DISTINCT FROM OLD.content_blob_key
               OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
               OR NEW.content_bytes IS DISTINCT FROM OLD.content_bytes
               OR NEW.created_at   IS DISTINCT FROM OLD.created_at
               OR NEW.valid_from   IS DISTINCT FROM OLD.valid_from
            THEN
                RAISE EXCEPTION
                    'document_versions is append-only except valid_to '
                    '(only valid_to may change via UPDATE)';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )

    # Column-scoped UPDATE grant. The trigger is the substantive
    # restriction; the column grant is the cheap outer fence so a
    # buggy caller cannot even attempt to update content_bytes etc.
    op.execute("GRANT UPDATE (valid_to) ON document_versions TO ingestion_worker;")

    # 3. document_poll_schedule — per-document polling state.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_poll_schedule (
            document_id      uuid PRIMARY KEY
                               REFERENCES documents(id) ON DELETE RESTRICT,
            cadence_interval interval NOT NULL,
            next_poll_at     timestamptz NOT NULL,
            last_polled_at   timestamptz,
            failure_count    int NOT NULL DEFAULT 0,
            CONSTRAINT document_poll_schedule_failure_count_nonneg
                CHECK (failure_count >= 0)
        );
        """
    )

    # The claim-loop hot path. ADR-0001 specifies:
    #   SELECT ... FROM document_poll_schedule
    #    WHERE next_poll_at <= now() AND failure_count <= 5
    #    ORDER BY next_poll_at
    #    FOR UPDATE SKIP LOCKED LIMIT N
    # Index on next_poll_at alone is sufficient — failure_count is a
    # cheap residual filter at the small fraction of parked rows
    # expected (kill-switch at >5 failures).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_poll_schedule_next_poll_at
            ON document_poll_schedule (next_poll_at);
        """
    )

    # 4. ingestion_incident — append-only log of upstream failures.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_incident (
            id          bigserial PRIMARY KEY,
            document_id uuid NOT NULL
                          REFERENCES documents(id) ON DELETE RESTRICT,
            error_class text NOT NULL,
            payload     jsonb NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )

    # Per-document incident history (admin "recent incidents for doc X").
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ingestion_incident_doc_occurred
            ON ingestion_incident (document_id, occurred_at DESC);
        """
    )

    # Global recent-incidents feed (admin health endpoint).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ingestion_incident_occurred_at
            ON ingestion_incident (occurred_at DESC);
        """
    )

    # 5. Ownership and grants.
    op.execute("ALTER TABLE document_poll_schedule OWNER TO schema_owner;")
    op.execute("ALTER TABLE ingestion_incident OWNER TO schema_owner;")
    op.execute("ALTER FUNCTION reject_document_version_update() OWNER TO schema_owner;")
    op.execute("ALTER SEQUENCE ingestion_incident_id_seq OWNER TO schema_owner;")

    op.execute("GRANT SELECT, INSERT, UPDATE ON document_poll_schedule TO ingestion_worker;")
    op.execute("GRANT SELECT, INSERT ON ingestion_incident TO ingestion_worker;")
    # The bigserial sequence needs USAGE so bigserial DEFAULT can advance
    # it under the ingestion_worker session.
    op.execute("GRANT USAGE ON SEQUENCE ingestion_incident_id_seq TO ingestion_worker;")

    # 6. Self-documentation.
    op.execute(
        "COMMENT ON TABLE document_poll_schedule IS "
        "'Per-document polling cadence and last-claim state. PK is "
        "document_id (1:1 with documents). The SKIP LOCKED claim loop "
        "from ADR-0001 reads next_poll_at; the worker bumps it after "
        "each poll. failure_count >5 parks the row and emits an "
        "ingestion_incident.';"
    )
    op.execute(
        "COMMENT ON TABLE ingestion_incident IS "
        "'Append-only log of upstream-ingestion failures and parks. "
        "Surfaced by /v1/admin/health/ingestion. Append-only by grant "
        "(no UPDATE/DELETE on the role); no trigger needed.';"
    )
    op.execute(
        "COMMENT ON COLUMN document_versions.version_no IS "
        "'Monotonic version number within a document. Nullable until "
        "WU3.4 populates it; tightened in a later migration.';"
    )
    op.execute(
        "COMMENT ON COLUMN document_versions.valid_from IS "
        "'Inclusive lower bound of when this version was the live "
        "version, per the ingestion worker. Set at row insertion.';"
    )
    op.execute(
        "COMMENT ON COLUMN document_versions.valid_to IS "
        "'Upper bound of when this version was observed live. Extended "
        "by the worker on every unchanged poll; closed (final timestamp) "
        "when a successor version is inserted. The only mutable column "
        "on this table (see reject_document_version_update()).';"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ingestion_incident;")
    op.execute("DROP TABLE IF EXISTS document_poll_schedule;")

    # Restore the strict append-only trigger body.
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
    op.execute("REVOKE UPDATE (valid_to) ON document_versions FROM ingestion_worker;")

    op.execute("DROP INDEX IF EXISTS idx_document_versions_doc_valid_to;")
    op.execute(
        "ALTER TABLE document_versions "
        "DROP CONSTRAINT IF EXISTS document_versions_unique_doc_version_no;"
    )
    op.execute(
        """
        ALTER TABLE document_versions
            DROP COLUMN IF EXISTS valid_to,
            DROP COLUMN IF EXISTS valid_from,
            DROP COLUMN IF EXISTS version_no;
        """
    )

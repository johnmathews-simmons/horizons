"""Add the ``change_events`` table — the load-bearing read artefact.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-05

WU3.4 — the per-document poll transaction writes one ``change_events``
row per residual clause-pairing from the alignment pipeline. This
migration creates the table the worker writes into and the API will
read from for the three primitives (discovery / temporal /
differential) at corpus / document / clause scope (doc 3 §"Implications
for the three primitives").

WU1.2 originally scheduled a stub shape (``id, document_id,
jurisdiction, sector, change_type, alignment_confidence, detected_at,
effective_date``) but deferred the table; WU3.4 ships the full
real-column shape directly so WU4.4 / WU5.3 can render diffs without a
second widening migration.

Shape:
- ``id`` bigserial PK (matches the WU3.1 ``ingestion_incident``
  precedent — append-only logs key off bigserial; ``document_versions``
  / ``clauses`` use ``uuidv7()`` because they need pre-known IDs at
  insert time).
- ``document_id`` + ``document_version_id`` FKs to the corpus aggregate
  the event was detected against. The version FK is the *new* version
  — the one whose alignment against its predecessor produced the
  event.
- ``jurisdiction`` / ``sector`` denormalised onto the row so the
  doc-3 composite index ``(jurisdiction, sector, detected_at,
  effective_date)`` answers the discovery hot path without joining
  ``documents``.
- ``change_type`` text + CHECK in
  ``{ADDED, REMOVED, MODIFIED, MOVED}`` (mirrors
  ``alignment.ChangeType`` Literal).
- before/after clause UIDs (uuid, nullable per change_type),
  before/after path (text — the ``Clause.path`` tuple serialised to a
  ``/``-joined string for legibility), before/after text (text), and
  ``alignment_confidence`` (double precision; raw float in ``(0, 1]``
  from the pipeline).
- ``detected_at`` default ``now()`` (when the worker wrote the row).
- ``effective_date`` ``timestamptz NULL`` — populated from the new
  ``document_versions.effective_date`` when available; doc 3 spells
  out the per-jurisdiction-default-lag fallback.

Multi-tenant isolation (CLAUDE.md "Multi-tenant isolation is two-axis
and load-bearing from day one"):
- RLS enabled + FORCE'd; subscription-scope policy on ``TO api_app``
  mirrors the WU1.4 corpus policies (``EXISTS(... current_scope()
  ...)``). A UK-only client sees zero EU change events.
- ``ingestion_worker`` carries an explicit pass-through policy so
  alignment writes succeed under RLS-on.
- ``admin_bypass`` gets ``SELECT`` (BYPASSRLS bypasses the policy but
  not the grant). No write path for admins.

Append-only enforcement: a ``BEFORE UPDATE OR DELETE`` trigger
rejects every mutation. The corpus is immutable history; once a
change event is written, it stays. Mistakes are corrected by inserting
a corrective row (rare; never on the demo path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. The table.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS change_events (
            id                     bigserial PRIMARY KEY,
            document_id            uuid NOT NULL
                                     REFERENCES documents(id) ON DELETE RESTRICT,
            document_version_id    uuid NOT NULL
                                     REFERENCES document_versions(id) ON DELETE RESTRICT,
            jurisdiction           text NOT NULL,
            sector                 text NOT NULL,
            change_type            text NOT NULL,
            before_clause_uid      uuid,
            after_clause_uid       uuid,
            before_path            text,
            after_path             text,
            before_text            text,
            after_text             text,
            alignment_confidence   double precision NOT NULL,
            detected_at            timestamptz NOT NULL DEFAULT now(),
            effective_date         timestamptz,
            CONSTRAINT change_events_change_type_chk
                CHECK (change_type IN ('ADDED', 'REMOVED', 'MODIFIED', 'MOVED')),
            CONSTRAINT change_events_confidence_range_chk
                CHECK (alignment_confidence > 0.0 AND alignment_confidence <= 1.0)
        );
        """
    )

    # 2. Indexes.
    # Discovery hot path (doc 3): "what changed in <scope> over <window>".
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_change_events_scope
            ON change_events (jurisdiction, sector, detected_at, effective_date);
        """
    )
    # Per-document temporal lookup ("all events for this document").
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_change_events_document
            ON change_events (document_id, detected_at);
        """
    )
    # Per-version replay (rebuilding what the worker produced for a
    # given version — useful for backfills and for the orphan-blob sweep
    # walking the version_id → blob_key map).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_change_events_version
            ON change_events (document_version_id);
        """
    )

    # 3. Ownership.
    op.execute("ALTER TABLE change_events OWNER TO schema_owner;")
    op.execute("ALTER SEQUENCE change_events_id_seq OWNER TO schema_owner;")

    # 4. Append-only enforcement (UPDATE + DELETE both rejected).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_change_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'change_events is append-only; UPDATE / DELETE not permitted';
        END;
        $$;
        """
    )
    op.execute("ALTER FUNCTION reject_change_event_mutation() OWNER TO schema_owner;")
    op.execute(
        """
        DROP TRIGGER IF EXISTS change_events_no_update ON change_events;
        CREATE TRIGGER change_events_no_update
            BEFORE UPDATE ON change_events
            FOR EACH ROW EXECUTE FUNCTION reject_change_event_mutation();
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS change_events_no_delete ON change_events;
        CREATE TRIGGER change_events_no_delete
            BEFORE DELETE ON change_events
            FOR EACH ROW EXECUTE FUNCTION reject_change_event_mutation();
        """
    )

    # 5. Grants.
    #
    # api_app reads only — RLS narrows the visible set to the
    # caller's subscription scope.
    op.execute("GRANT SELECT ON change_events TO api_app;")

    # ingestion_worker writes (and reads back for sweeping / replay).
    op.execute("GRANT SELECT, INSERT ON change_events TO ingestion_worker;")
    op.execute("GRANT USAGE ON SEQUENCE change_events_id_seq TO ingestion_worker;")

    # admin_bypass reads for support tooling. BYPASSRLS bypasses the
    # policy but not the grant.
    op.execute("GRANT SELECT ON change_events TO admin_bypass;")

    # 6. RLS spine. Pattern matches WU1.4's corpus tables exactly.
    op.execute("ALTER TABLE change_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE change_events FORCE ROW LEVEL SECURITY;")

    op.execute("DROP POLICY IF EXISTS change_events_in_scope ON change_events;")
    op.execute(
        """
        CREATE POLICY change_events_in_scope ON change_events
            FOR SELECT TO api_app
            USING (
                EXISTS (
                    SELECT 1 FROM app_private.current_scope() cs
                    WHERE cs.jurisdiction = change_events.jurisdiction
                      AND cs.sector       = change_events.sector
                )
            );
        """
    )

    op.execute("DROP POLICY IF EXISTS change_events_ingestion_all ON change_events;")
    op.execute(
        """
        CREATE POLICY change_events_ingestion_all ON change_events
            FOR ALL TO ingestion_worker
            USING (true) WITH CHECK (true);
        """
    )

    # 7. Self-documentation.
    op.execute(
        "COMMENT ON TABLE change_events IS "
        "'Precomputed clause-level change events. One row per residual "
        "pairing from the alignment pipeline. Subscription-scope RLS "
        "filters api_app reads to the caller''s (jurisdiction, sector). "
        "Append-only via trigger.';"
    )
    op.execute(
        "COMMENT ON COLUMN change_events.document_version_id IS "
        "'The new version whose alignment against its predecessor "
        "produced this event. before_text is from the predecessor; "
        "after_text is from the row referenced here.';"
    )
    op.execute(
        "COMMENT ON COLUMN change_events.alignment_confidence IS "
        "'Raw float in (0, 1]. 1.0 for residual ADDED/REMOVED clauses; "
        "0.9 floor for heading+content pairings; lower values from the "
        "content-similarity pass. Demo UI hides events below the "
        "configured threshold (default 0.6, see doc 3).';"
    )
    op.execute(
        "COMMENT ON COLUMN change_events.effective_date IS "
        "'When this change comes into force. Populated from the new "
        "document_versions.effective_date when available; null when "
        "neither the source nor the per-jurisdiction default lag yields "
        "a value (doc 3 §Principles 3).';"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS change_events_ingestion_all ON change_events;")
    op.execute("DROP POLICY IF EXISTS change_events_in_scope ON change_events;")
    op.execute("ALTER TABLE change_events NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE change_events DISABLE ROW LEVEL SECURITY;")

    op.execute("DROP TRIGGER IF EXISTS change_events_no_delete ON change_events;")
    op.execute("DROP TRIGGER IF EXISTS change_events_no_update ON change_events;")
    op.execute("DROP FUNCTION IF EXISTS reject_change_event_mutation();")

    op.execute("DROP INDEX IF EXISTS idx_change_events_version;")
    op.execute("DROP INDEX IF EXISTS idx_change_events_document;")
    op.execute("DROP INDEX IF EXISTS idx_change_events_scope;")

    op.execute("DROP TABLE IF EXISTS change_events;")

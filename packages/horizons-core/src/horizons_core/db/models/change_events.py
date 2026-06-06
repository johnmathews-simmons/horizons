"""ORM model for the ``change_events`` table.

One row per residual clause pairing emitted by the alignment pipeline
(see ``docs/RFC-2 clause-alignment.md``). Append-only — UPDATE / DELETE
are rejected by a trigger (WU3.4 migration 0010). RLS narrows reads
to rows whose ``(jurisdiction, sector)`` is in the caller's
subscription scope.

The ``id`` is ``bigserial`` (not ``uuidv7()`` like every other PK in
the schema) — append-only event logs key off bigserial; the precedent
is ``ingestion_incident``. The model uses ``BigInteger`` accordingly.

The ``(jurisdiction, sector)`` pair is denormalised onto the row so
the doc-3 discovery hot path uses the composite index
``idx_change_events_scope`` without joining ``documents``.

The ``before_text`` / ``after_text`` columns hold the full clause
bodies at the diff boundary — the differential primitive reads them
directly; no join through ``clauses`` is required to render a diff.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from horizons_core.db.models.base import Base


class ChangeEvent(Base):
    __tablename__ = "change_events"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('ADDED', 'REMOVED', 'MODIFIED', 'MOVED')",
            name="change_events_change_type_chk",
        ),
        CheckConstraint(
            "alignment_confidence > 0.0 AND alignment_confidence <= 1.0",
            name="change_events_confidence_range_chk",
        ),
        Index(
            "idx_change_events_scope",
            "jurisdiction",
            "sector",
            "detected_at",
            "effective_date",
        ),
        Index(
            "idx_change_events_document",
            "document_id",
            "detected_at",
        ),
        Index(
            "idx_change_events_version",
            "document_version_id",
        ),
        {
            "comment": (
                "Precomputed clause-level change events. One row per "
                "residual pairing from the alignment pipeline. "
                "Subscription-scope RLS filters api_app reads. "
                "Append-only via trigger."
            ),
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str] = mapped_column(Text, nullable=False)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    before_clause_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    after_clause_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    before_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    alignment_confidence: Mapped[float] = mapped_column(Double, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    effective_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

"""ORM model for the ``documents`` table.

Stable identity for an upstream legal text. A row here is the long-lived
handle for a single statute / regulation / guidance document; the
mutable content lives on attached ``document_versions`` rows. See
``db/schema.md`` for the aggregate description.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from horizons_core.db.models.base import Base

if TYPE_CHECKING:
    from horizons_core.db.models.versions import DocumentVersion


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "idx_documents_jurisdiction_sector",
            "jurisdiction",
            "sector",
        ),
        {
            "comment": (
                "Stable identity for an upstream legal text. Append-only via "
                "trigger; lawstronaut_document_id is the upstream key."
            ),
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str] = mapped_column(Text, nullable=False)
    lawstronaut_document_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document",
    )

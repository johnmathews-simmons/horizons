"""ORM model for the ``document_versions`` table.

Time-stamped re-issue of a ``documents`` row. The full marked-up
content lives in blob storage at ``(content_blob_container,
content_blob_key)``; the database row keeps the SHA-256 and byte count
for integrity. See ``db/schema.md`` for the aggregate description.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from horizons_core.db.models.base import Base

if TYPE_CHECKING:
    from horizons_core.db.models.clauses import Clause
    from horizons_core.db.models.documents import Document


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_label",
            name="document_versions_unique_label",
        ),
        CheckConstraint(
            "content_bytes >= 0",
            name="document_versions_content_bytes_nonneg",
        ),
        CheckConstraint(
            "octet_length(content_sha256) = 32",
            name="document_versions_sha256_length",
        ),
        Index(
            "idx_document_versions_doc_effective",
            "document_id",
            "effective_date",
        ),
        {
            "comment": (
                "Time-stamped re-issue of a document. Append-only via "
                "trigger. Content lives in blob storage at (container, "
                "key); the row keeps the sha256 and byte count for "
                "integrity."
            ),
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_label: Mapped[str] = mapped_column(Text, nullable=False)
    publication_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    effective_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    content_blob_container: Mapped[str] = mapped_column(Text, nullable=False)
    content_blob_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    document: Mapped[Document] = relationship(back_populates="versions")
    clauses: Mapped[list[Clause]] = relationship(
        back_populates="document_version",
    )

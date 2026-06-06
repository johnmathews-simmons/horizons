"""ORM model for the ``clauses`` table.

Heading-anchored fragment of a ``document_versions`` row. ``clause_uid``
carries clause identity across versions of the same document — the
alignment pipeline (see ``docs/RFC-2 clause-alignment.md``) is what assigns
matching uids when a new version lands. ``clause_path`` is the
positional label (e.g. ``Part 2 / Section 4 / (a) / (i)``) and is free
to renumber as neighbours change.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from horizons_core.db.models.base import Base

if TYPE_CHECKING:
    from horizons_core.db.models.versions import DocumentVersion


class Clause(Base):
    __tablename__ = "clauses"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "clause_path",
            name="clauses_unique_path_per_version",
        ),
        Index(
            "idx_clauses_version_ord",
            "document_version_id",
            "ord",
        ),
        Index(
            "idx_clauses_clause_uid",
            "clause_uid",
        ),
        {
            "comment": (
                "Heading-anchored fragment of a document_version. "
                "clause_uid carries identity across versions; clause_path "
                "is positional and renumbers freely. Append-only via "
                "trigger."
            ),
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    clause_uid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    clause_path: Mapped[str] = mapped_column(Text, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    ord: Mapped[int] = mapped_column(Integer, nullable=False)

    document_version: Mapped[DocumentVersion] = relationship(
        back_populates="clauses",
    )

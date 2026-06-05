"""ORM model for the ``watchlists`` table.

Per-user saved query / filter. The canonical private-state shape for
WU1.4: a ``user_id`` column, no append-only trigger (mutable), and four
RLS policies on ``api_app`` keyed off ``app.user_id``. See
``db/schema.md`` for the aggregate description and ``db/rls.md`` for
the policy shape.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from horizons_core.db.models.base import Base

if TYPE_CHECKING:
    from horizons_core.db.models.documents import Document
    from horizons_core.db.models.users import User


class Watchlist(Base):
    __tablename__ = "watchlists"
    __table_args__ = (
        Index("idx_watchlists_user_id", "user_id"),
        Index("idx_watchlists_document_id", "document_id"),
        UniqueConstraint("user_id", "document_id", name="watchlists_user_document_unique"),
        {
            "comment": (
                "User -> watched document. Two-axis isolation: cross-client "
                "privacy via owner-keyed RLS; subscription-scope via the "
                "watchlists_in_subscription_scope trigger."
            ),
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    user: Mapped[User] = relationship()
    document: Mapped[Document] = relationship()

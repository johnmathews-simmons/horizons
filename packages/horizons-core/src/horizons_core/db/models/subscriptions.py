"""ORM models for ``subscriptions`` and ``subscription_scopes``.

Time-bounded entitlements and their (jurisdiction, sector) coverage.
See ``db/schema.md`` for the aggregate description and append-only
trigger contract.
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
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from horizons_core.db.models.base import Base

if TYPE_CHECKING:
    from horizons_core.db.models.users import User


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="subscriptions_valid_to_after_valid_from",
        ),
        Index(
            "idx_subscriptions_user_id_valid_from",
            "user_id",
            "valid_from",
        ),
        {
            "comment": (
                "Time-bounded entitlements. Append-only via trigger: only "
                "permitted UPDATE is valid_to NULL -> timestamp."
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
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    user: Mapped[User] = relationship(back_populates="subscriptions")
    scopes: Mapped[list[SubscriptionScope]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )


class SubscriptionScope(Base):
    __tablename__ = "subscription_scopes"
    __table_args__ = (
        PrimaryKeyConstraint(
            "subscription_id",
            "jurisdiction",
            "sector",
            name="subscription_scopes_pkey",
        ),
        {
            "comment": (
                "Jurisdiction x sector coverage per subscription. "
                "Append-only via trigger: only permitted UPDATE is "
                "valid_to NULL -> timestamp (WU4.5 soft-delete)."
            ),
        },
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str] = mapped_column(Text, nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    subscription: Mapped[Subscription] = relationship(back_populates="scopes")

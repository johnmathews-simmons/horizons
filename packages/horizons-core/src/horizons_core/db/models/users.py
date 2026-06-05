"""ORM model for the ``users`` table.

Account identity. A row here persists across the customer's lifetime;
cancelling and resubscribing produces new ``subscriptions`` rows, not
new ``users`` rows. See ``db/schema.md`` for the full aggregate
description.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from horizons_core.db.models.base import Base

if TYPE_CHECKING:
    from horizons_core.db.models.subscriptions import Subscription


class UserRole(enum.StrEnum):
    """Role values for ``users.role``.

    Mirrors the Postgres ENUM type ``user_role`` created in migration
    ``0002_tenancy_tables``. Adding a value requires the matching
    ``ALTER TYPE user_role ADD VALUE`` migration.
    """

    CLIENT = "client"
    ADMIN = "admin"


def _user_role_values(enum_cls: type[UserRole]) -> list[str]:
    return [member.value for member in enum_cls]


class User(Base):
    __tablename__ = "users"
    __table_args__ = {
        "comment": (
            "Account identity. Mutable (password / email change). "
            "Persists across cancel/resubscribe cycles."
        ),
    }

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=_user_role_values,
            create_type=False,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

"""ORM model for the ``admin_access_log`` table.

Append-only audit trail for cross-tenant admin sessions (operator and
impersonation). The ``core.auth.admin`` context managers write exactly
one row per session on entry. See ``db/schema.md`` for the aggregate
description and ``db/rls.md`` §Admin code paths for the architecture.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from horizons_core.db.models.base import Base

if TYPE_CHECKING:
    from horizons_core.db.models.users import User


class AdminAccessMode(enum.StrEnum):
    """Mode values for ``admin_access_log.mode``.

    Mirrors the Postgres ENUM type ``admin_access_mode`` created in
    migration ``0006_admin_access_log``.
    """

    OPERATOR = "operator"
    IMPERSONATION = "impersonation"


def _admin_access_mode_values(enum_cls: type[AdminAccessMode]) -> list[str]:
    return [member.value for member in enum_cls]


class AdminAccessLog(Base):
    __tablename__ = "admin_access_log"
    __table_args__ = (
        CheckConstraint(
            "(mode = 'operator' AND target_user_id IS NULL) "
            "OR (mode = 'impersonation' AND target_user_id IS NOT NULL)",
            name="admin_access_log_mode_target_consistent",
        ),
        Index(
            "idx_admin_access_log_admin_id_granted_at",
            "admin_id",
            "granted_at",
        ),
        Index(
            "idx_admin_access_log_target_user_id_granted_at",
            "target_user_id",
            "granted_at",
        ),
        {
            "comment": (
                "Append-only audit row per admin operator or impersonation "
                "session (WU1.9). One row written by core.auth.admin context "
                "managers on entry. token_id reserved for Track-4 JWT id "
                "binding."
            ),
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    mode: Mapped[AdminAccessMode] = mapped_column(
        Enum(
            AdminAccessMode,
            name="admin_access_mode",
            values_callable=_admin_access_mode_values,
            create_type=False,
        ),
        nullable=False,
    )
    token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    admin: Mapped[User] = relationship(foreign_keys=[admin_id])
    target_user: Mapped[User | None] = relationship(foreign_keys=[target_user_id])

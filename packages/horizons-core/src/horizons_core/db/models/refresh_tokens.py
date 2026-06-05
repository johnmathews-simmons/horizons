"""ORM model for the ``refresh_tokens`` table.

Server-side refresh-token registry. One row per issued refresh token,
keyed on the JWT's ``jti``. ``revoked_at`` is null until the row is
retired by logout or refresh rotation. See ``db/schema.md`` for the
aggregate description.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from horizons_core.db.models.base import Base

if TYPE_CHECKING:
    from horizons_core.db.models.users import User


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index(
            "idx_refresh_tokens_user_id_issued_at",
            "user_id",
            "issued_at",
        ),
        {
            "comment": (
                "Server-side refresh-token registry (WU4.0). One row per "
                "issued refresh token, keyed on JWT jti. revoked_at NULL = "
                "live; set = retired. Refresh endpoint queries by jti under "
                "owner-RLS."
            ),
        },
    )

    jti: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship()

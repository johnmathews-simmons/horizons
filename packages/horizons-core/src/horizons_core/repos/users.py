"""``UsersRepository`` and its DTO.

Account identity reads. Used by the login flow to resolve an email to a
``user_id`` + password hash, and by ``/v1/me`` to fetch the calling user's
own row. The table currently carries no RLS (tenancy RLS on
``users`` / ``subscriptions`` / ``subscription_scopes`` is deferred until
an endpoint reads them directly without going through
``app_private.current_scope()``); the repo runs under whichever role the
session bracket selected.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from horizons_core.db.models.users import User, UserRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class UserDTO(BaseModel):
    """Serialisable view of a ``users`` row."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    email: str
    password_hash: str
    role: UserRole
    created_at: datetime


class UsersRepository:
    """Read users by email or id.

    Writes (create / password rotation / role change) belong elsewhere —
    today the only writer is the admin path, which is out of scope until
    WU4.5. The repo is intentionally read-only for now.
    """

    dto_type: ClassVar[type[BaseModel]] = UserDTO

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_email(self, email: str) -> UserDTO | None:
        """Look up a user by exact email; returns ``None`` if absent."""
        row = (
            await self._session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        return UserDTO.model_validate(row) if row is not None else None

    async def get_by_id(self, user_id: uuid.UUID) -> UserDTO | None:
        """Look up a user by primary key; returns ``None`` if absent."""
        row = (
            await self._session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        return UserDTO.model_validate(row) if row is not None else None

    async def list_by_role(
        self,
        role: UserRole,
        *,
        limit: int,
        offset: int,
    ) -> list[UserDTO]:
        """Return up to ``limit`` users with ``role``, oldest-first.

        Stable ordering on ``(created_at ASC, id ASC)`` so paginated
        admin views can rely on offset-based paging without rows
        shuffling between pages. Newer signups land after the current
        page, not in the middle of it.
        """
        rows = (
            (
                await self._session.execute(
                    select(User)
                    .where(User.role == role)
                    .order_by(User.created_at.asc(), User.id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [UserDTO.model_validate(r) for r in rows]

    async def count_by_role(self, role: UserRole) -> int:
        """Total rows for ``role`` — companion to ``list_by_role``."""
        count = (
            await self._session.execute(
                select(func.count()).select_from(User).where(User.role == role)
            )
        ).scalar_one()
        return int(count)

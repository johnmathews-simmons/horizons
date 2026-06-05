"""``RefreshTokensRepository`` and its DTO.

Server-side registry for refresh-token revocation. The auth layer
(``core.auth.local_jwt``) records a row at issuance, the refresh
endpoint queries by ``jti`` to confirm a token is still live, and
logout / rotation set ``revoked_at``.

Reads and writes run as ``api_app`` with ``app.user_id`` bound. The
RLS policy (``refresh_tokens_owner_select`` / _insert / _update) keys
on the same GUC, so the repo's keyword-only ``user_id`` argument is the
explicit ownership claim at the call site — the policy is the second
layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update

from horizons_core.db.models.refresh_tokens import RefreshToken

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RefreshTokenDTO(BaseModel):
    """Serialisable view of a ``refresh_tokens`` row."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    jti: uuid.UUID
    user_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class RefreshTokensRepository:
    """Owner-scoped registry for issued refresh tokens.

    The session is injected at construction. The repo never opens,
    commits, or closes the session.
    """

    dto_type: ClassVar[type[BaseModel]] = RefreshTokenDTO

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        jti: uuid.UUID,
        user_id: uuid.UUID,
        issued_at: datetime,
        expires_at: datetime,
    ) -> RefreshTokenDTO:
        """Insert one refresh-token row.

        The caller-supplied ``user_id`` must match the bound
        ``app.user_id``; the RLS ``WITH CHECK`` predicate enforces the
        same equality as a second layer.
        """
        row = RefreshToken(
            jti=jti,
            user_id=user_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return RefreshTokenDTO.model_validate(row)

    async def get_by_jti(self, jti: uuid.UUID) -> RefreshTokenDTO | None:
        """Look up by ``jti``; returns ``None`` if absent or out of scope."""
        row = (
            await self._session.execute(
                select(RefreshToken).where(RefreshToken.jti == jti)
            )
        ).scalar_one_or_none()
        return RefreshTokenDTO.model_validate(row) if row is not None else None

    async def revoke(
        self,
        *,
        jti: uuid.UUID,
        user_id: uuid.UUID,
        revoked_at: datetime,
    ) -> bool:
        """Mark a refresh-token row revoked; return whether it changed.

        Idempotent: re-revoking an already-revoked row leaves
        ``revoked_at`` at its original value. Returns ``False`` if the
        row is not visible to the current session (out of scope, or
        absent) or already had ``revoked_at`` set.
        """
        # RETURNING the PK is the typed-clean way to learn whether the
        # UPDATE matched a row: `CursorResult.rowcount` exists at runtime
        # but is not in the typed surface of session.execute()'s Result.
        result = await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.jti == jti,
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
            .returning(RefreshToken.jti)
        )
        return result.scalar_one_or_none() is not None

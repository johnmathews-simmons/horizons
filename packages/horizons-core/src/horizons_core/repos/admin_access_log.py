"""``AdminAccessLogRepository`` and its DTO.

Append-only audit log for cross-tenant admin sessions. Writes are issued
by the ``core.auth.admin`` context managers (WU1.9) and, later in
Track 4, by the token-mint / refresh endpoints. Reads are exposed for
admin tooling and the integration suite's verification steps.

The table is invisible to ``api_app`` and ``ingestion_worker``; only
``admin_bypass`` carries the SELECT + INSERT grant. The repo therefore
assumes the calling session has assumed the ``admin_bypass`` role —
this is what the WU1.9 context managers guarantee.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from horizons_core.db.models.admin_access_log import AdminAccessLog, AdminAccessMode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class AdminAccessLogDTO(BaseModel):
    """Serialisable view of an ``admin_access_log`` row."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    admin_id: uuid.UUID
    target_user_id: uuid.UUID | None
    mode: AdminAccessMode
    token_id: uuid.UUID | None
    reason: str | None
    granted_at: datetime


class AdminAccessLogRepository:
    """Append-only writes + chronological reads of admin sessions.

    The session is injected at construction. The repo never opens,
    commits, or closes the session — bracket discipline lives in
    ``core.auth.admin``.
    """

    dto_type: ClassVar[type[BaseModel]] = AdminAccessLogDTO

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        mode: AdminAccessMode,
        admin_id: uuid.UUID,
        target_user_id: uuid.UUID | None,
        token_id: uuid.UUID | None,
        reason: str | None,
    ) -> AdminAccessLogDTO:
        """Insert one audit row and return its DTO.

        Caller-supplied invariants: ``operator`` mode requires
        ``target_user_id`` to be ``None``; ``impersonation`` requires
        it to be set. The database CHECK constraint enforces the same
        rule as a second layer.
        """
        row = AdminAccessLog(
            admin_id=admin_id,
            target_user_id=target_user_id,
            mode=mode,
            token_id=token_id,
            reason=reason,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return AdminAccessLogDTO.model_validate(row)

    async def list_for_admin(self, admin_id: uuid.UUID) -> list[AdminAccessLogDTO]:
        """Every audit row written by ``admin_id``, newest first."""
        rows = (
            (
                await self._session.execute(
                    select(AdminAccessLog)
                    .where(AdminAccessLog.admin_id == admin_id)
                    .order_by(AdminAccessLog.granted_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [AdminAccessLogDTO.model_validate(r) for r in rows]

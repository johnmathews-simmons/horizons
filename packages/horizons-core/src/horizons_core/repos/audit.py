"""``AdminAccessLogReadRepository`` — filtered reads of admin_access_log.

The write side lives in
:mod:`horizons_core.repos.admin_access_log` and is owned by the WU1.9
``core.auth.admin`` context managers; this module is read-only by
design. WU7.4 exposes ``GET /v1/admin/audit?...`` on top of it.

Filters are all optional and AND'd together:

- ``since`` (``datetime``) — return rows with ``granted_at >= since``.
  Defaults to ``now() - 24h`` at the *route* layer; this repo trusts
  whatever the caller passes.
- ``admin_id`` (``uuid``) — restrict to one admin's writes.
- ``target_user_id`` (``uuid``) — restrict to impersonation rows
  targeting a specific user. ``operator``-mode rows have
  ``target_user_id IS NULL`` and are excluded when this filter is set.
- ``action`` (``AdminAccessMode``) — restrict to ``operator`` or
  ``impersonation`` rows.
- ``limit`` (``int``) — page size cap. The route clamps to 500; this
  repo trusts the caller.

Rows are returned newest-first (``granted_at DESC``). The repo does
not paginate beyond the limit; cursor-based pagination is a later
work-unit if the audit surface grows.

This repository assumes the calling session has assumed the
``admin_bypass`` role — that is what the WU1.9 / WU4.5 admin context
managers guarantee.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from horizons_core.db.models.admin_access_log import AdminAccessLog, AdminAccessMode
from horizons_core.repos.admin_access_log import AdminAccessLogDTO

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class AdminAccessLogReadRepository:
    """Filtered reads of the append-only admin audit trail.

    The companion writer repo (:class:`AdminAccessLogRepository`) lives
    next door; both surfaces use the same DTO shape so admin tooling
    can read what audited paths wrote without an additional mapping
    layer.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        *,
        since: datetime | None = None,
        admin_id: uuid.UUID | None = None,
        target_user_id: uuid.UUID | None = None,
        action: AdminAccessMode | None = None,
        limit: int = 100,
    ) -> list[AdminAccessLogDTO]:
        """Return matching rows newest-first, capped at ``limit``."""
        stmt = select(AdminAccessLog).order_by(AdminAccessLog.granted_at.desc()).limit(limit)
        if since is not None:
            stmt = stmt.where(AdminAccessLog.granted_at >= since)
        if admin_id is not None:
            stmt = stmt.where(AdminAccessLog.admin_id == admin_id)
        if target_user_id is not None:
            stmt = stmt.where(AdminAccessLog.target_user_id == target_user_id)
        if action is not None:
            stmt = stmt.where(AdminAccessLog.mode == action)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [AdminAccessLogDTO.model_validate(r) for r in rows]

"""``/v1/admin/audit`` — paginated read surface for ``admin_access_log``.

One GET, admin-only, with five optional filters: ``since`` (defaults to
now - 24h), ``admin_id``, ``target_user_id``, ``action``, and
``limit`` (silently clamped to 500).

Reads only. The audit table is append-only at the database layer
(WU1.9: no UPDATE / DELETE policy or grant; the architectural test in
``tests/test_admin_access_log_append_only.py`` keeps that invariant
visible). This route never writes to it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from horizons_core.core.auth import Principal
from horizons_core.db.models.admin_access_log import AdminAccessMode
from horizons_core.repos.admin_access_log import AdminAccessLogDTO
from horizons_core.repos.audit import AdminAccessLogReadRepository
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import admin_operator_session_for_request, require_admin_principal

router = APIRouter(prefix="/v1/admin/audit", tags=["admin"])

_DEFAULT_LIMIT: int = 100
_MAX_LIMIT: int = 500
_DEFAULT_LOOKBACK = timedelta(hours=24)


class AdminAccessLogRow(BaseModel):
    """Wire shape for one ``admin_access_log`` row."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    admin_id: uuid.UUID
    target_user_id: uuid.UUID | None
    mode: AdminAccessMode
    token_id: uuid.UUID | None
    reason: str | None
    granted_at: datetime


class AdminAuditResponse(BaseModel):
    """Result envelope: rows + the filters that produced them.

    Echoing the effective filters lets the SPA render "showing X rows
    since YYYY-MM-DD" without re-computing the defaults itself.
    """

    model_config = ConfigDict(frozen=True)

    since: datetime
    limit: int
    count: int
    rows: list[AdminAccessLogRow]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _to_row(dto: AdminAccessLogDTO) -> AdminAccessLogRow:
    return AdminAccessLogRow(
        id=dto.id,
        admin_id=dto.admin_id,
        target_user_id=dto.target_user_id,
        mode=dto.mode,
        token_id=dto.token_id,
        reason=dto.reason,
        granted_at=dto.granted_at,
    )


@router.get("", response_model=AdminAuditResponse)
async def search_admin_audit(  # noqa: PLR0913 — each parameter is a wire filter or dep
    response: Response,
    _admin: Annotated[Principal, Depends(require_admin_principal)],
    session: Annotated[AsyncSession, Depends(admin_operator_session_for_request)],
    since: Annotated[
        datetime | None,
        Query(description="Inclusive lower bound on granted_at; defaults to now - 24h."),
    ] = None,
    admin_id: Annotated[
        uuid.UUID | None,
        Query(description="Restrict to one admin's writes."),
    ] = None,
    target_user_id: Annotated[
        uuid.UUID | None,
        Query(description="Restrict to impersonation rows targeting this user id."),
    ] = None,
    action: Annotated[
        AdminAccessMode | None,
        Query(description="Restrict to 'operator' or 'impersonation' rows."),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, description="Page size cap. Silently clamped to 500."),
    ] = _DEFAULT_LIMIT,
) -> AdminAuditResponse:
    """Filtered, paginated reads of the admin audit log."""
    _no_store(response)

    effective_since = since if since is not None else datetime.now(UTC) - _DEFAULT_LOOKBACK
    effective_limit = min(limit, _MAX_LIMIT)

    dtos = await AdminAccessLogReadRepository(session).search(
        since=effective_since,
        admin_id=admin_id,
        target_user_id=target_user_id,
        action=action,
        limit=effective_limit,
    )

    rows = [_to_row(d) for d in dtos]
    return AdminAuditResponse(
        since=effective_since,
        limit=effective_limit,
        count=len(rows),
        rows=rows,
    )

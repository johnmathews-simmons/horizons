"""``GET /v1/admin/clients`` — paginated list of ``role='client'`` users.

The list is the entry point into the admin client-detail and
support-view flows: the SPA renders it as a table and links each row
to ``/admin/clients/{id}``. Read-only. Strictly client users — admin
accounts never appear; surfacing them would clutter the operator UX
and create a path to "impersonate another admin" that the impersonate
endpoint already refuses.

The route depends on ``admin_operator_session_for_request`` which
writes one ``admin_access_log`` row per request (``mode='operator'``,
``target_user_id=NULL``) **before** the route body runs. That is the
audit defence against the "admin enumerates client identifiers without
leaving a trail" adversary class: every list fetch is itself a logged
admin event, regardless of whether the body succeeds or raises.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from horizons_core.core.auth import Principal
from horizons_core.db.models.users import UserRole
from horizons_core.repos.users import UsersRepository
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import admin_operator_session_for_request, require_admin_principal

router = APIRouter(prefix="/v1/admin/clients", tags=["admin"])

_DEFAULT_LIMIT: int = 50
_MAX_LIMIT: int = 200


class ClientRow(BaseModel):
    """Wire shape for one row of the admin clients table."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    email: str
    created_at: datetime


class ClientsListResponse(BaseModel):
    """Result envelope: rows + paging echo."""

    model_config = ConfigDict(frozen=True)

    limit: int
    offset: int
    total: int
    count: int
    rows: list[ClientRow]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@router.get("", response_model=ClientsListResponse)
async def list_clients(
    response: Response,
    _admin: Annotated[Principal, Depends(require_admin_principal)],
    session: Annotated[AsyncSession, Depends(admin_operator_session_for_request)],
    limit: Annotated[
        int,
        Query(ge=1, description=f"Page size cap. Silently clamped to {_MAX_LIMIT}."),
    ] = _DEFAULT_LIMIT,
    offset: Annotated[
        int,
        Query(ge=0, description="Number of rows to skip."),
    ] = 0,
) -> ClientsListResponse:
    """Return ``role='client'`` users, oldest-first, with a total count.

    Admins (``role='admin'``) are excluded by construction — the
    support-view flow only impersonates clients, and surfacing other
    admins on this list would be both noisy and a small vector for
    "the operator clicked the wrong row" mistakes.

    Ordering is stable on ``(created_at ASC, id ASC)`` so offset paging
    behaves predictably across new signups.
    """
    _no_store(response)

    effective_limit = min(limit, _MAX_LIMIT)

    repo = UsersRepository(session)
    dtos = await repo.list_by_role(
        UserRole.CLIENT,
        limit=effective_limit,
        offset=offset,
    )
    total = await repo.count_by_role(UserRole.CLIENT)

    rows = [ClientRow(id=d.id, email=d.email, created_at=d.created_at) for d in dtos]
    return ClientsListResponse(
        limit=effective_limit,
        offset=offset,
        total=total,
        count=len(rows),
        rows=rows,
    )

"""``/v1/me/watchlists`` — CRUD on per-user watched-document rows.

Three operations:

- ``GET /v1/me/watchlists`` — list the caller's watches.
- ``POST /v1/me/watchlists`` — add one; body ``{document_id, name?}``.
  Service-layer scope check: the document's ``(jurisdiction, sector)``
  must be in the caller's current subscription scope. A mismatch
  returns ``422`` *before* the database trigger fires.
- ``DELETE /v1/me/watchlists/{watchlist_id}`` — remove one. A row owned
  by another user is invisible (RLS) and returns ``404`` — not 403, to
  avoid leaking the existence of foreign rows.

All three responses carry ``Cache-Control: private, no-store`` per the
contract in ``docs/api/auth.md``.

Defence-in-depth posture for the scope axis:

1. **Service layer (this file)** — reads the caller's scope set, asserts
   the document is within it. Clean 422 on violation.
2. **Database trigger** (``watchlists_in_subscription_scope``, WU4.3
   migration 0009) — raises ``check_violation`` on any INSERT/UPDATE OF
   document_id that lands a row outside scope. Catches direct
   repository / SQL paths that bypass the service layer.
3. **Cross-client privacy** is unchanged from WU1.4: the
   ``watchlists_owner_*`` RLS policies key on ``app.user_id``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from horizons_core.core.auth import Principal
from horizons_core.core.subscriptions import current_scope_pairs
from horizons_core.repos.documents import DocumentsRepository
from horizons_core.repos.watchlists import WatchlistDTO, WatchlistsRepository
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import authenticated_user, session_for_request

router = APIRouter(prefix="/v1/me/watchlists", tags=["watchlists"])


class WatchlistResponse(BaseModel):
    """Wire shape for a single watchlist row."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    document_id: uuid.UUID
    name: str
    created_at: str  # ISO-8601; Pydantic encodes datetime → str on serialise


class CreateWatchlistRequest(BaseModel):
    """``POST /v1/me/watchlists`` request body."""

    model_config = ConfigDict(frozen=True)

    document_id: uuid.UUID
    name: str | None = None


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _to_response(dto: WatchlistDTO) -> WatchlistResponse:
    return WatchlistResponse(
        id=dto.id,
        document_id=dto.document_id,
        name=dto.name,
        created_at=dto.created_at.isoformat(),
    )


@router.get("", response_model=list[WatchlistResponse])
async def list_watchlists(
    response: Response,
    _principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request)],
) -> list[WatchlistResponse]:
    """Every watchlist the caller owns (RLS filters)."""
    _no_store(response)
    rows = await WatchlistsRepository(session).list_for()
    return [_to_response(r) for r in rows]


@router.post(
    "",
    response_model=WatchlistResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_watchlist(
    body: CreateWatchlistRequest,
    response: Response,
    principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request)],
) -> WatchlistResponse:
    """Add a watchlist for ``body.document_id``; validate scope first."""
    _no_store(response)

    # The document must be visible to the caller AND its (jurisdiction,
    # sector) must intersect the caller's subscription scope. The
    # repository's RLS-narrowed get_by_id already filters by scope, so a
    # missing return covers both "document doesn't exist" and "document
    # exists but is out of scope". The distinction is irrelevant for the
    # response — either case is a service-layer scope violation.
    document = await DocumentsRepository(session).get_by_id(body.document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="document is outside your subscription scope",
        )
    scope = await current_scope_pairs(session)
    if (document.jurisdiction, document.sector) not in scope:
        # Belt-and-braces: if RLS ever loosened, this is the second
        # service-layer guard. Same body so the response is uniform.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="document is outside your subscription scope",
        )

    name = body.name if body.name else document.title
    dto = await WatchlistsRepository(session).create(
        user_id=principal.user_id,
        document_id=body.document_id,
        name=name,
    )
    return _to_response(dto)


@router.delete(
    "/{watchlist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_watchlist(
    watchlist_id: uuid.UUID,
    response: Response,
    principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request)],
) -> Response:
    """Remove one of the caller's watchlists, or 404."""
    _no_store(response)
    removed = await WatchlistsRepository(session).delete(
        user_id=principal.user_id,
        watchlist_id=watchlist_id,
    )
    if not removed:
        # RLS filters foreign rows out of the UPDATE/DELETE surface, so
        # "row not visible" and "row not found" look identical on the
        # repository side. Returning 404 avoids leaking foreign-row
        # existence.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="watchlist not found",
        )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response

"""``/v1/admin/subscriptions`` — admin CRUD on a client's tenancy ledger.

Three operations, all admin-only:

- ``GET  /v1/admin/subscriptions?user_id=<uuid>`` — list a target
  user's subscriptions and their scope rows (active + soft-deleted).
- ``POST /v1/admin/subscriptions`` — create a new subscription for a
  target user with a scope set.
- ``PATCH /v1/admin/subscriptions/{id}`` — add or remove scope rows on
  an existing subscription. Append-only:
    - adds → new ``subscription_scopes`` rows.
    - removes → ``valid_to`` set on existing rows (soft-delete; no row
      deleted).
  Removing a scope triggers a soft-hide pass over the owning user's
  watchlists: any watchlist whose document is no longer in the user's
  active scope gets ``active=false`` (no row deleted, no row's
  ``document_id`` mutated).

Auth posture: all three routes depend on ``require_admin_principal``
(401 → 403 for non-admin). The working session is yielded by
``admin_operator_session_for_request``, which writes one
``admin_access_log`` row per request *before* the route body runs — so
the audit trail persists even if the route raises.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from horizons_core.core.auth import Principal
from horizons_core.repos import (
    SubscriptionDTO,
    SubscriptionsRepository,
    UsersRepository,
    WatchlistsRepository,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import admin_operator_session_for_request, require_admin_principal

router = APIRouter(prefix="/v1/admin/subscriptions", tags=["admin"])


# ---- wire models ---------------------------------------------------------


class ScopePairBody(BaseModel):
    """One ``(jurisdiction, sector)`` pair in admin requests."""

    model_config = ConfigDict(frozen=True)

    jurisdiction: str = Field(min_length=1)
    sector: str = Field(min_length=1)


class CreateSubscriptionRequest(BaseModel):
    """``POST /v1/admin/subscriptions`` request body."""

    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    scopes: list[ScopePairBody] = Field(min_length=1)
    valid_from: datetime | None = None


def _empty_scope_pairs() -> list[ScopePairBody]:
    """Default factory for the PATCH request's empty lists.

    Wrapped as a named function so pyright's strict variance check on
    ``Field(default_factory=...)`` resolves the return type without
    needing a ``cast``.
    """
    return []


class PatchSubscriptionRequest(BaseModel):
    """``PATCH /v1/admin/subscriptions/{id}`` request body."""

    model_config = ConfigDict(frozen=True)

    add_scopes: list[ScopePairBody] = Field(default_factory=_empty_scope_pairs)
    remove_scopes: list[ScopePairBody] = Field(default_factory=_empty_scope_pairs)


class SubscriptionScopeOut(BaseModel):
    """Response shape for one scope row."""

    model_config = ConfigDict(frozen=True)

    jurisdiction: str
    sector: str
    valid_to: datetime | None


class SubscriptionOut(BaseModel):
    """Response shape for a subscription + its scopes."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    valid_from: datetime
    valid_to: datetime | None
    created_at: datetime
    scopes: list[SubscriptionScopeOut]


class SubscriptionsListResponse(BaseModel):
    """Response shape for the list endpoint."""

    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    subscriptions: list[SubscriptionOut]


class PatchSubscriptionResponse(BaseModel):
    """Response shape for the PATCH endpoint."""

    model_config = ConfigDict(frozen=True)

    subscription: SubscriptionOut
    scopes_added: int
    scopes_removed: int
    watchlists_soft_hidden: int


# ---- helpers -------------------------------------------------------------


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _to_out(dto: SubscriptionDTO) -> SubscriptionOut:
    return SubscriptionOut(
        id=dto.id,
        user_id=dto.user_id,
        valid_from=dto.valid_from,
        valid_to=dto.valid_to,
        created_at=dto.created_at,
        scopes=[
            SubscriptionScopeOut(
                jurisdiction=s.jurisdiction,
                sector=s.sector,
                valid_to=s.valid_to,
            )
            for s in dto.scopes
        ],
    )


def _pair_set(pairs: list[ScopePairBody]) -> set[tuple[str, str]]:
    return {(p.jurisdiction, p.sector) for p in pairs}


# ---- routes --------------------------------------------------------------


@router.get("", response_model=SubscriptionsListResponse)
async def list_subscriptions(
    response: Response,
    user_id: Annotated[uuid.UUID, Query(description="Target client user id")],
    _admin: Annotated[Principal, Depends(require_admin_principal)],
    session: Annotated[AsyncSession, Depends(admin_operator_session_for_request)],
) -> SubscriptionsListResponse:
    """List ``user_id``'s subscriptions and scope history.

    Returns ``404`` if no such user exists. Returns an empty
    ``subscriptions`` list if the user exists but has none.
    """
    _no_store(response)

    user = await UsersRepository(session).get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )

    subs = await SubscriptionsRepository(session).list_for_user(user_id)
    return SubscriptionsListResponse(
        user_id=user_id,
        subscriptions=[_to_out(s) for s in subs],
    )


@router.post(
    "",
    response_model=SubscriptionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_subscription(
    body: CreateSubscriptionRequest,
    response: Response,
    _admin: Annotated[Principal, Depends(require_admin_principal)],
    session: Annotated[AsyncSession, Depends(admin_operator_session_for_request)],
) -> SubscriptionOut:
    """Create a subscription for ``body.user_id``.

    The target user must exist (404 otherwise). ``valid_from`` defaults
    to ``now()`` UTC if omitted. The scope list must be non-empty
    (Pydantic enforces); duplicate ``(jurisdiction, sector)`` pairs in
    the request are deduplicated server-side before insert.
    """
    _no_store(response)

    user = await UsersRepository(session).get_by_id(body.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )

    valid_from = body.valid_from if body.valid_from is not None else datetime.now(UTC)
    scopes = sorted(_pair_set(body.scopes))

    dto = await SubscriptionsRepository(session).create_for_user(
        user_id=body.user_id,
        valid_from=valid_from,
        scopes=scopes,
    )
    return _to_out(dto)


@router.patch("/{subscription_id}", response_model=PatchSubscriptionResponse)
async def patch_subscription(  # noqa: PLR0913 — each parameter is a wire field or dep
    subscription_id: uuid.UUID,
    body: PatchSubscriptionRequest,
    response: Response,
    _admin: Annotated[Principal, Depends(require_admin_principal)],
    session: Annotated[AsyncSession, Depends(admin_operator_session_for_request)],
) -> PatchSubscriptionResponse:
    """Add and / or soft-delete scopes on ``subscription_id``.

    Workflow:

    1. Resolve the subscription; 404 if absent.
    2. Reject UPDATEs touching no scopes (no-op PATCH is 422 — keeps the
       admin client honest about why they called us).
    3. Detect overlap: a ``(jurisdiction, sector)`` cannot appear in
       both ``add_scopes`` and ``remove_scopes`` (422). Adds must not
       already exist as an active scope on this subscription (422).
       Removes must currently be active on this subscription (422).
    4. Apply adds first, then removes (the order is irrelevant for
       correctness but happens to be how the trigger works — INSERT
       paths land before UPDATE paths).
    5. Compute the user's post-reduction active scope set, derive the
       in-scope document ids, soft-hide every active watchlist for the
       user whose ``document_id`` is *not* in that set. The same logic
       runs even when only adds happened (cheap no-op: every active
       watchlist's document stays in scope).
    """
    _no_store(response)

    if not body.add_scopes and not body.remove_scopes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="patch requires add_scopes and/or remove_scopes",
        )

    add_pairs = _pair_set(body.add_scopes)
    remove_pairs = _pair_set(body.remove_scopes)
    if add_pairs & remove_pairs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scope pair cannot be in both add_scopes and remove_scopes",
        )

    repo = SubscriptionsRepository(session)
    existing = await repo.get_by_id(subscription_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="subscription not found",
        )

    active_pairs = {
        (s.jurisdiction, s.sector) for s in existing.scopes if s.valid_to is None
    }
    if any(p in active_pairs for p in add_pairs):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cannot add a scope already active on this subscription",
        )
    if any(p not in active_pairs for p in remove_pairs):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cannot remove a scope that is not active on this subscription",
        )

    scopes_added = 0
    if add_pairs:
        scopes_added = await repo.add_scopes(
            subscription_id=subscription_id,
            scopes=sorted(add_pairs),
        )

    now = datetime.now(UTC)
    scopes_removed = 0
    if remove_pairs:
        scopes_removed = await repo.soft_delete_scopes(
            subscription_id=subscription_id,
            scopes=sorted(remove_pairs),
            ended_at=now,
        )

    in_scope_docs = await repo.active_scope_documents(user_id=existing.user_id)
    watchlists_hidden = await WatchlistsRepository(session).soft_hide_out_of_scope(
        user_id=existing.user_id,
        in_scope_document_ids=in_scope_docs,
    )

    after = await repo.get_by_id(subscription_id)
    assert after is not None  # not deleted; we just touched it
    return PatchSubscriptionResponse(
        subscription=_to_out(after),
        scopes_added=scopes_added,
        scopes_removed=scopes_removed,
        watchlists_soft_hidden=watchlists_hidden,
    )

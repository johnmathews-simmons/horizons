"""``GET /v1/me`` and ``GET /v1/me/overview`` — the calling user plus their
subscription summary, and the corpus-matrix dashboard endpoint.

The WU4.1 stub echoed the JWT principal. WU4.3 replaces it with a real
read through the repository layer plus the subscription summary derived
from ``app_private.current_scope()`` and the active rows in
``subscriptions``.

``GET /v1/me/overview`` (WU5.1 / Task 5) powers the HomeView dashboard.
It calls ``corpus_shape()`` (the full jurisdiction × sector matrix) and
overlays per-axis ``subscribed`` flags from the caller's subscription.
Admin callers use ``admin_or_app_session_dep`` so they see the full
corpus; every entry is ``subscribed=true`` for admins because admins
are not subscription-scoped.

Both endpoints carry ``Cache-Control: private, no-store`` so no
intermediary or browser cache retains the per-user body. The same
posture applies to every other per-user endpoint (watchlists, etc) and
is documented in ``docs/api/auth.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from horizons_core.core.auth import Principal, Role
from horizons_core.core.corpus import change_event_shape, corpus_shape
from horizons_core.core.subscriptions import (
    SubscriptionSummaryDTO,
    current_scope_pairs,
    current_subscription_summary,
)
from horizons_core.repos.users import UsersRepository
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import authenticated_user, session_for_request
from horizons_api.deps.admin_or_app import admin_or_app_session_dep

router = APIRouter(prefix="/v1", tags=["me"])


# ----- /v1/me models ----------------------------------------------------------


class MeResponse(BaseModel):
    """The wire shape for ``GET /v1/me`` (WU4.3 real implementation)."""

    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    email: str
    role: str
    created_at: datetime
    subscription: SubscriptionSummaryDTO


# ----- /v1/me/overview models -------------------------------------------------


class JurisdictionOverviewItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    document_count: int
    change_count: int
    subscribed: bool


class SectorOverviewItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    document_count: int
    change_count: int
    subscribed: bool


class OverviewTotals(BaseModel):
    model_config = ConfigDict(frozen=True)

    documents: int
    jurisdictions: int
    sectors: int
    subscribed_jurisdictions: int
    subscribed_sectors: int


class OverviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_admin: bool
    totals: OverviewTotals
    jurisdictions: list[JurisdictionOverviewItem]
    sectors: list[SectorOverviewItem]


# ----- /v1/me handler ---------------------------------------------------------


@router.get("/me", response_model=MeResponse)
async def get_me(
    response: Response,
    principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(session_for_request)],
) -> MeResponse:
    """Return the user row + subscription summary for the verified bearer."""
    response.headers["Cache-Control"] = "private, no-store"

    user = await UsersRepository(session).get_by_id(principal.user_id)
    if user is None:
        # JWT was signed by us and not expired, but the user row is gone
        # (deleted account; unlikely outside admin / cleanup paths).
        # Treat as an authentication failure so the client must re-login.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="account not found",
        )

    summary = await current_subscription_summary(session)
    return MeResponse(
        user_id=user.id,
        email=user.email,
        role=user.role.value,
        created_at=user.created_at,
        subscription=summary,
    )


# ----- /v1/me/overview handler ------------------------------------------------


@router.get("/me/overview", response_model=OverviewResponse)
async def get_me_overview(
    response: Response,
    principal: Annotated[Principal, Depends(authenticated_user)],
    session: Annotated[AsyncSession, Depends(admin_or_app_session_dep)],
) -> OverviewResponse:
    """Return the corpus-matrix dashboard overview for the calling user.

    Admins see the full corpus with every entry ``subscribed=true``.
    Clients see the full corpus matrix but with ``subscribed`` flags
    reflecting their own subscription scope.
    """
    response.headers["Cache-Control"] = "private, no-store"

    matrix = await corpus_shape(session)
    change_matrix = await change_event_shape(session)

    is_admin = principal.role == Role.ADMIN

    if is_admin:
        scope_pairs: set[tuple[str, str]] = set()
    else:
        scope_pairs = await current_scope_pairs(session)

    subscribed_jurisdictions = {j for (j, _) in scope_pairs}
    subscribed_sectors = {s for (_, s) in scope_pairs}

    # Roll up by jurisdiction
    juris_counts: dict[str, int] = {}
    for row in matrix:
        juris_counts[row.jurisdiction] = juris_counts.get(row.jurisdiction, 0) + row.document_count

    # Roll up by sector
    sector_counts: dict[str, int] = {}
    for row in matrix:
        sector_counts[row.sector] = sector_counts.get(row.sector, 0) + row.document_count

    # Per-(jurisdiction, sector) change-event counts. Roll up to the
    # same axes as the corpus matrix; defaults to 0 for any axis present
    # in the corpus but absent from change_matrix.
    juris_change_counts: dict[str, int] = {}
    sector_change_counts: dict[str, int] = {}
    for crow in change_matrix:
        juris_change_counts[crow.jurisdiction] = (
            juris_change_counts.get(crow.jurisdiction, 0) + crow.change_count
        )
        sector_change_counts[crow.sector] = (
            sector_change_counts.get(crow.sector, 0) + crow.change_count
        )

    jurisdictions = sorted(
        [
            JurisdictionOverviewItem(
                code=code,
                document_count=count,
                change_count=juris_change_counts.get(code, 0),
                subscribed=is_admin or code in subscribed_jurisdictions,
            )
            for code, count in juris_counts.items()
        ],
        key=lambda item: item.code,
    )

    sectors = sorted(
        [
            SectorOverviewItem(
                code=code,
                document_count=count,
                change_count=sector_change_counts.get(code, 0),
                subscribed=is_admin or code in subscribed_sectors,
            )
            for code, count in sector_counts.items()
        ],
        key=lambda item: item.code,
    )

    total_docs = sum(row.document_count for row in matrix)

    if is_admin:
        sub_juris_count = len(juris_counts)
        sub_sector_count = len(sector_counts)
    else:
        sub_juris_count = len(subscribed_jurisdictions & juris_counts.keys())
        sub_sector_count = len(subscribed_sectors & sector_counts.keys())

    totals = OverviewTotals(
        documents=total_docs,
        jurisdictions=len(juris_counts),
        sectors=len(sector_counts),
        subscribed_jurisdictions=sub_juris_count,
        subscribed_sectors=sub_sector_count,
    )

    return OverviewResponse(
        is_admin=is_admin,
        totals=totals,
        jurisdictions=jurisdictions,
        sectors=sectors,
    )

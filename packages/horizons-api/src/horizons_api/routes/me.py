"""``GET /v1/me`` — the calling user plus their subscription summary.

The WU4.1 stub echoed the JWT principal. WU4.3 replaces it with a real
read through the repository layer plus the subscription summary derived
from ``app_private.current_scope()`` and the active rows in
``subscriptions``.

The response always carries ``Cache-Control: private, no-store`` so no
intermediary or browser cache retains the per-user body. The same
posture applies to every other per-user endpoint (watchlists, etc) and
is documented in ``docs/api/auth.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from horizons_core.core.auth import Principal
from horizons_core.core.subscriptions import (
    SubscriptionSummaryDTO,
    current_subscription_summary,
)
from horizons_core.repos.users import UsersRepository
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import authenticated_user, session_for_request

router = APIRouter(prefix="/v1", tags=["me"])


class MeResponse(BaseModel):
    """The wire shape for ``GET /v1/me`` (WU4.3 real implementation)."""

    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    email: str
    role: str
    created_at: datetime
    subscription: SubscriptionSummaryDTO


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

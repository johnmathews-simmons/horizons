"""``POST /v1/admin/impersonate`` — mint an audited impersonation token.

The endpoint is the only sanctioned mint path for
``TokenKind.IMPERSONATION`` bearers. The flow:

1. The caller must be an authenticated admin (``role='admin'``).
2. The target ``user_id`` must exist and have ``role='client'``.
   Admin → admin impersonation is refused (422); admin self-impersonation
   is refused (422). Both are well-formed requests that we refuse on
   policy grounds, not malformed input.
3. ``admin_impersonation_session`` is entered to commit an
   ``admin_access_log`` row with ``mode='impersonation'`` and
   ``target_user_id = target``. The row's transaction commits **before**
   the working session is yielded, so the audit row persists even if the
   token mint or response shaping below raises.

   Note: this route writes **two** audit rows per successful mint —
   one ``mode='operator'`` (from the ``admin_operator_session_for_request``
   dep, recording "admin Y entered ``/v1/admin/impersonate`` at T")
   and one ``mode='impersonation'`` (from this call, recording "admin
   Y began impersonating client X at T"). The split is intentional:
   the operator row records the URL hit even when the impersonation
   refuses downstream (404 / 422 — see step 2), and the impersonation
   row records the elevation event proper. Tests pin both rows so a
   future refactor that drops either source cannot quietly elide
   audit signal.
4. Only after the audit row has committed do we mint the impersonation
   JWT. The token's ``sub`` is the target client and ``role='client'``;
   RLS / role gating fire as they would for a real client request.
5. The token, the target email, and the original admin's id + email are
   returned. The original admin's email lets the SPA render the
   "Support view — viewing CLIENT_EMAIL · Exit (return as ADMIN_EMAIL)"
   banner without a second round trip.

Exit is intentionally client-side: the SPA drops the impersonation
token from in-memory state and resumes its admin session. The 15-minute
token TTL bounds the impersonation window; a separate exit-audit row
adds no durable signal beyond what the entry row already records, and
the entry row is the single authoritative event for "admin Y
impersonated client X at time T".
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from horizons_core.core.auth import Principal, TokenKind, TokenProvider
from horizons_core.core.auth.admin import admin_impersonation_session
from horizons_core.db.models.users import UserRole
from horizons_core.repos.users import UsersRepository
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import (
    admin_operator_session_for_request,
    get_token_provider,
    require_admin_principal,
)

router = APIRouter(prefix="/v1/admin/impersonate", tags=["admin"])

# Mirrors the LocalJwtProvider default IMPERSONATION TTL (15 min). Echoed
# in the response so the SPA can render the banner countdown and know
# when to clear local state proactively rather than waiting for the
# first 401.
_IMPERSONATION_TTL_SECONDS: int = 15 * 60


class ImpersonateRequest(BaseModel):
    """``POST /v1/admin/impersonate`` request body."""

    model_config = ConfigDict(frozen=True)

    target_user_id: uuid.UUID
    reason: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Free-text justification — recorded on the admin_access_log "
            "row. The mint endpoint does not enforce non-empty: a blank "
            "reason is allowed but discouraged; the SPA's confirm dialog "
            "will press the operator for one."
        ),
    )


class ImpersonateResponse(BaseModel):
    """Response shape — the token plus everything the SPA banner needs."""

    model_config = ConfigDict(frozen=True)

    impersonation_token: str
    target_user_id: uuid.UUID
    target_email: str
    original_admin_id: uuid.UUID
    original_admin_email: str
    expires_in_seconds: int


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@router.post(
    "",
    response_model=ImpersonateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def begin_impersonation(  # noqa: PLR0913 — each parameter is a wire field or dep
    body: ImpersonateRequest,
    response: Response,
    admin: Annotated[Principal, Depends(require_admin_principal)],
    operator_session: Annotated[
        AsyncSession, Depends(admin_operator_session_for_request)
    ],
    provider: Annotated[TokenProvider, Depends(get_token_provider)],
) -> ImpersonateResponse:
    """Mint an audited impersonation token for ``body.target_user_id``.

    Validation order is deliberate so the audit story is clean:

    1. Resolve the *admin* (we already have the principal, but we need
       the admin's email for the response). The lookup runs under the
       ``admin_operator_session_for_request`` ``admin_bypass`` session
       that was opened to write the dependency's *own* audit row — that
       row records the lookup itself, which is the right semantic.
    2. Resolve the target. A missing or non-client target is refused
       BEFORE the impersonation audit row is written, so a typo'd
       ``target_user_id`` does not leave an "impersonated NULL" row.
    3. Refuse self-impersonation and admin-target impersonation; both
       are policy refusals, not malformed input → 422.
    4. Enter ``admin_impersonation_session`` purely to commit the
       impersonation audit row. We immediately exit the with-block; the
       working session is never used because the route does not need to
       read or write as the target — it only needs to mint a token.
    5. Mint the impersonation JWT. The minting itself does not touch the
       database; if it raises, the audit row already records the
       attempted elevation.
    6. Shape the response. ``Cache-Control: private, no-store`` per
       contract.
    """
    _no_store(response)

    users = UsersRepository(operator_session)

    admin_row = await users.get_by_id(admin.user_id)
    # The principal was already validated upstream; a missing row here
    # would mean the admin's account was deleted mid-flight. Treat as a
    # 401 so the SPA reauthenticates rather than silently 500ing.
    if admin_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="account not found",
        )

    target_row = await users.get_by_id(body.target_user_id)
    if target_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="target user not found",
        )
    if target_row.id == admin.user_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cannot impersonate yourself",
        )
    if target_row.role is not UserRole.CLIENT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="target is not a client",
        )

    # Open + immediately close the impersonation session. The audit row
    # is written and committed *before* the working session is yielded
    # (see core.auth.admin._record_audit_row), so we get the row
    # durably without taking a write dependency on the working session
    # at all. The reason text rides on the audit row.
    async with admin_impersonation_session(
        admin.user_id,
        target_row.id,
        reason=body.reason,
    ):
        pass

    token = await provider.issue_token(
        user_id=target_row.id,
        role=UserRole.CLIENT.value,
        kind=TokenKind.IMPERSONATION,
    )

    return ImpersonateResponse(
        impersonation_token=token,
        target_user_id=target_row.id,
        target_email=target_row.email,
        original_admin_id=admin.user_id,
        original_admin_email=admin_row.email,
        expires_in_seconds=_IMPERSONATION_TTL_SECONDS,
    )


__all__ = ["ImpersonateRequest", "ImpersonateResponse", "router"]

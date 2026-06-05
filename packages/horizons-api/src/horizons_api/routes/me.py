"""``GET /v1/me`` — minimal authenticated stub.

WU4.1 ships a deliberate stub: the route depends on
``authenticated_user`` and echoes the verified Principal as proof
that the auth pipeline is wired end-to-end. WU4.3 will replace this
with the real implementation: user row + subscription summary
fetched through the repository layer, ``Cache-Control: private,
no-store`` header, etc. Keeping the path stable across WUs means the
webapp can be wired to it now without a follow-up rename.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from horizons_core.core.auth import Principal
from pydantic import BaseModel, ConfigDict

from horizons_api.deps import authenticated_user

router = APIRouter(prefix="/v1", tags=["me"])


class MeResponse(BaseModel):
    """The wire shape for ``GET /v1/me`` (WU4.1 stub)."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    role: str
    kind: str


@router.get("/me", response_model=MeResponse)
def get_me(
    principal: Annotated[Principal, Depends(authenticated_user)],
) -> MeResponse:
    return MeResponse(
        user_id=str(principal.user_id),
        role=principal.role,
        kind=principal.kind.value,
    )

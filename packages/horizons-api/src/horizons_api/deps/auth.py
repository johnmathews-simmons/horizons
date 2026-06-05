"""``authenticated_user`` — bearer-token authentication dependency.

Extracts ``Authorization: Bearer <token>`` from the request, hands the
token to the ``TokenProvider`` for verification, and returns a
``Principal``. Missing or invalid bearer raises ``HTTPException(401)``
with a generic message — the verifier's specific error reason is not
echoed back to the client (that would leak internals).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from horizons_core.core.auth import InvalidTokenError, Principal, TokenProvider

from horizons_api.deps.provider import get_token_provider

# ``auto_error=False`` makes FastAPI hand back ``None`` for missing /
# malformed Authorization headers instead of raising 403 by itself —
# we want to control the status code (401) and the body shape.
_bearer_scheme = HTTPBearer(auto_error=False)


def authenticated_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    provider: Annotated[
        TokenProvider,
        Depends(get_token_provider),
    ],
) -> Principal:
    """Return the ``Principal`` for the request, or raise 401.

    The 401 body intentionally does not distinguish missing vs
    invalid vs expired — the verifier's specific reason is logged for
    operations but never echoed to clients, because the distinction
    leaks token-validation internals.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return provider.verify_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

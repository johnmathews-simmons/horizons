"""Dependencies for the refresh / logout flows.

Refresh and logout differ from the rest of the API in two ways:

1. The token they accept is a **refresh-kind** JWT, not access-kind. The
   bearer source is therefore *either* a cookie (browser) *or* the
   Authorization header (programmatic). ``require_refresh_principal``
   accepts both shapes; the route doesn't care which side it came from.

2. After verification, the caller's session must be bound to the
   ``principal.user_id`` so that the ``refresh_tokens`` RLS policy fires
   for the revoke / rotate writes. ``session_for_refresh`` is the
   shape-equivalent of ``session_for_request`` but keyed off the
   refresh-kind dep instead of the access-kind one.

A refresh token presented as the access-token bearer to ``/v1/me`` is
already rejected by ``require_kind(ACCESS)``; the symmetric defence —
an access token presented to refresh — lives here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from horizons_core.core.auth import (
    InvalidTokenError,
    Principal,
    TokenKind,
    TokenProvider,
)
from horizons_core.db.session import get_session, set_local_role

from horizons_api.deps.provider import get_token_provider

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


REFRESH_COOKIE_NAME = "refresh_token"

_bearer_scheme = HTTPBearer(auto_error=False)


def _extract_refresh_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    """Return the refresh token string from header or cookie, or ``None``.

    Header wins if both are present. In practice browsers will only have
    the cookie and programmatic clients will only have the header, so the
    ordering doesn't bite; documenting the precedence prevents surprise.
    """
    if credentials is not None and credentials.credentials:
        return credentials.credentials
    cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    return cookie or None


def require_refresh_principal(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    provider: Annotated[TokenProvider, Depends(get_token_provider)],
) -> Principal:
    """Validate a refresh-kind JWT from cookie or bearer header.

    Missing, invalid, expired, or wrong-kind tokens raise the same 401 —
    the body is uniform so the client cannot distinguish branches.
    Liveness against ``refresh_tokens`` is checked inside the route, not
    here, because revocation is a database round-trip the dep should not
    own.
    """
    token = _extract_refresh_token(request, credentials)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        principal = provider.verify_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if principal.kind is not TokenKind.REFRESH:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


async def session_for_refresh(
    principal: Annotated[Principal, Depends(require_refresh_principal)],
) -> AsyncGenerator[AsyncSession]:
    """Yield a session bound to ``principal.user_id`` under ``api_app``."""
    async with get_session(principal.user_id) as session:
        await set_local_role(session, "api_app")
        yield session

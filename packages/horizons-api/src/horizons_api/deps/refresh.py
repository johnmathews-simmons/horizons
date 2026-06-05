"""Dependencies for the refresh / logout flows.

Refresh and logout differ from the rest of the API in two ways:

1. The token they accept is a **refresh-kind** JWT, not access-kind. The
   bearer source is therefore *either* a cookie (browser) *or* the
   Authorization header (programmatic). ``require_refresh_principal``
   accepts both shapes and *records which side it came from* — that
   provenance is the only safe driver of the response shape (see
   security note below).

2. After verification, the caller's session must be bound to the
   ``principal.user_id`` so that the ``refresh_tokens`` RLS policy fires
   for the revoke / rotate writes. ``session_for_refresh`` is the
   shape-equivalent of ``session_for_request`` but keyed off the
   refresh-kind dep instead of the access-kind one.

A refresh token presented as the access-token bearer to ``/v1/me`` is
already rejected by ``require_kind(ACCESS)``; the symmetric defence —
an access token presented to refresh — lives here.

Security note — cookie-source binding
-------------------------------------
A naive design would let the client choose the response shape via the
``X-Client-Type: browser`` header on refresh / logout. That is unsafe:
XSS-controlled JS on the SPA's origin can call ``fetch('/v1/auth/
refresh')`` — the browser attaches the ``HttpOnly`` cookie
automatically — and *omit* ``X-Client-Type: browser`` to coerce the
server into returning the rotated refresh token in a JS-readable JSON
body, defeating ``HttpOnly``. The same trick applies to logout (the
clearing cookie would not be sent, and the server would still revoke
the row but the cookie stays alive in the browser for a future
re-attack window).

Mitigation: bind the response shape to the *token source* on refresh /
logout. If the cookie was the source, the response is always
browser-shaped (no refresh token in body; ``Set-Cookie`` on rotation;
clearing ``Set-Cookie`` on logout) regardless of any client-controlled
header. The ``RefreshTokenSource`` enum is the dep's authoritative
signal; routes must use it.
"""

from __future__ import annotations

import enum
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


class RefreshTokenSource(enum.StrEnum):
    """Where the validated refresh token was extracted from.

    Used by refresh / logout routes to bind the response shape to the
    token source. See module docstring for the security rationale.
    """

    COOKIE = "cookie"
    HEADER = "header"


def _extract_refresh_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> tuple[str, RefreshTokenSource] | None:
    """Return ``(token, source)`` from header or cookie, or ``None``.

    Header wins when both are present so a programmatic caller that
    happens to share a cookie jar with the same browser session can
    still drive the refresh / logout flow explicitly. In practice
    browsers only have the cookie and programmatic clients only have
    the header, but the precedence is explicit so the security
    contract is unambiguous: the source the route gets is the one
    that *actually carried* the token through to verification.
    """
    if credentials is not None and credentials.credentials:
        return credentials.credentials, RefreshTokenSource.HEADER
    cookie = request.cookies.get(REFRESH_COOKIE_NAME)
    if cookie:
        return cookie, RefreshTokenSource.COOKIE
    return None


def require_refresh_principal(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    provider: Annotated[TokenProvider, Depends(get_token_provider)],
) -> tuple[Principal, RefreshTokenSource]:
    """Validate a refresh-kind JWT and return ``(principal, source)``.

    Missing, invalid, expired, or wrong-kind tokens raise the same 401 —
    the body is uniform so the client cannot distinguish branches.
    Liveness against ``refresh_tokens`` is checked inside the route, not
    here, because revocation is a database round-trip the dep should not
    own.

    The returned ``source`` is the *only* safe signal for response
    shaping (see module docstring). Routes MUST NOT shape the response
    from ``X-Client-Type`` on refresh / logout.
    """
    extracted = _extract_refresh_token(request, credentials)
    if extracted is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token, source = extracted
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
    return principal, source


async def session_for_refresh(
    verified: Annotated[tuple[Principal, RefreshTokenSource], Depends(require_refresh_principal)],
) -> AsyncGenerator[AsyncSession]:
    """Yield a session bound to the verified principal's ``user_id``."""
    principal, _source = verified
    async with get_session(principal.user_id) as session:
        await set_local_role(session, "api_app")
        yield session

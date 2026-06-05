"""Bearer-token authentication dependencies.

Each authenticated route declares which ``TokenKind`` it accepts by
depending on ``require_kind(<kind>)`` rather than a single
``authenticated_user``. The factory shape forces the call site to
state its expectation explicitly — a refresh token presented as a
bearer to a non-refresh endpoint is rejected at the auth boundary,
not deep in a handler.

``authenticated_user`` is the convenience alias for the dominant
case (``require_kind(TokenKind.ACCESS)``); refresh-handling endpoints
(WU4.2's ``/v1/auth/refresh``) depend on
``require_kind(TokenKind.REFRESH)``; impersonation tokens (WU4.5)
will have their own dependency in the admin surface.

The 401 body intentionally does not distinguish missing vs malformed
vs expired vs wrong-kind — the verifier's specific reason is logged
for operations but never echoed to clients, because the distinction
leaks token-validation internals.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from horizons_core.core.auth import (
    InvalidTokenError,
    Principal,
    TokenKind,
    TokenProvider,
)
from horizons_core.observability.logging import user_id_var

from horizons_api.deps.provider import get_token_provider

# ``auto_error=False`` makes FastAPI hand back ``None`` for missing /
# malformed Authorization headers instead of raising 403 by itself —
# we want to control the status code (401) and the body shape.
_bearer_scheme = HTTPBearer(auto_error=False)


def _verify_bearer(
    credentials: HTTPAuthorizationCredentials | None,
    provider: TokenProvider,
) -> Principal:
    """Shared bearer-extract + verify body used by every kind dep."""
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


def require_kind(kind: TokenKind):  # type: ignore[no-untyped-def]
    """Build a FastAPI dependency that requires a specific token kind.

    The returned callable is the dep injected into a route. A token
    presented with the wrong ``kind`` claim is rejected with 401 —
    the same status code as any other auth failure, so a client
    presenting a refresh token where an access token was expected
    cannot tell from the response whether the token was wrong-kind,
    wrong-signature, or absent.

    Annotation note: the return type is left implicit so FastAPI's
    dependency resolver sees the *closure* (it has the right
    signature) rather than a typed wrapper. Mypy / pyright still
    follow the Principal return through the closure's annotation.
    """

    def _dependency(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(_bearer_scheme),
        ],
        provider: Annotated[
            TokenProvider,
            Depends(get_token_provider),
        ],
    ) -> Principal:
        principal = _verify_bearer(credentials, provider)
        if principal.kind is not kind:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Bind user_id for log enrichment in the same place we know the
        # principal is valid. No reset — FastAPI runs each request in a
        # fresh asyncio task, so the contextvar is request-scoped via
        # task locals; pairing with the GUC bracket in deps/session.py
        # keeps the log value and the RLS value in lock-step.
        user_id_var.set(str(principal.user_id))
        return principal

    _dependency.__name__ = f"require_kind_{kind.value}"
    return _dependency


# The dominant case. Routes that want an ordinary authenticated
# request depend on ``authenticated_user``; refresh / impersonation
# routes build their own dep via ``require_kind``.
authenticated_user = require_kind(TokenKind.ACCESS)

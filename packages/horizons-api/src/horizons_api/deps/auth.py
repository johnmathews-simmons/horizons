"""Bearer-token authentication dependencies.

Each authenticated route declares which set of ``TokenKind`` values it
accepts by depending on ``require_kind(<kind>)`` or
``require_kinds(<kind>, <kind>, ...)`` rather than a single
``authenticated_user``. The factory shape forces the call site to
state its expectation explicitly — a refresh token presented as a
bearer to a non-refresh endpoint is rejected at the auth boundary,
not deep in a handler.

``authenticated_user`` is the alias for the dominant "human user
acting on their own behalf" case and accepts both
``TokenKind.ACCESS`` and ``TokenKind.IMPERSONATION``. The latter is
the audited admin support-view carrier minted by
``POST /v1/admin/impersonate``: its ``sub`` is the impersonated
client and its ``role`` is ``client``, so RLS and role gating fire
exactly as they would for a real client request. The elevation is
recorded once by the mint endpoint (``admin_access_log`` with
``mode='impersonation'``) and bounded by the 15-minute token TTL;
this contract trades per-request observability of impersonation for
the simplicity of "the SPA carries one bearer in support view". The
distinction between an impersonated and a direct client request is
not visible to client-facing routes by design.

Refresh-handling endpoints (WU4.2's ``/v1/auth/refresh``) depend on
``require_kind(TokenKind.REFRESH)``; admin endpoints layer
``require_admin_principal`` on top of ``authenticated_user`` and so
reject impersonation tokens by virtue of their ``role='client'``
claim. A refresh token presented as a bearer to any non-refresh
endpoint is still rejected.

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


def require_kinds(*kinds: TokenKind):  # type: ignore[no-untyped-def]
    """Build a FastAPI dependency that accepts any of ``kinds``.

    The returned callable is the dep injected into a route. A token
    whose ``kind`` claim is not in ``kinds`` is rejected with 401 —
    the same status code as any other auth failure, so a client
    presenting (say) a refresh token where ``{ACCESS, IMPERSONATION}``
    were expected cannot tell from the response whether the token was
    wrong-kind, wrong-signature, or absent.

    Annotation note: the return type is left implicit so FastAPI's
    dependency resolver sees the *closure* (it has the right
    signature) rather than a typed wrapper. Mypy / pyright still
    follow the Principal return through the closure's annotation.
    """
    if not kinds:
        raise ValueError("require_kinds requires at least one TokenKind")
    accepted = frozenset(kinds)

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
        if principal.kind not in accepted:
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

    _dependency.__name__ = "require_kinds_" + "_".join(sorted(k.value for k in accepted))
    return _dependency


def require_kind(kind: TokenKind):  # type: ignore[no-untyped-def]
    """Build a FastAPI dependency that requires exactly one ``kind``.

    Thin wrapper around ``require_kinds`` for the single-kind case.
    Kept as the call shape for refresh-only endpoints where the
    semantics are unambiguously "one kind, no others".
    """
    return require_kinds(kind)


# The dominant "human user acting on their own behalf" case. Accepts
# ACCESS (real client request) and IMPERSONATION (admin in support
# view, audited at mint by /v1/admin/impersonate). Refresh-handling
# endpoints build their own dep via ``require_kind(TokenKind.REFRESH)``.
authenticated_user = require_kinds(TokenKind.ACCESS, TokenKind.IMPERSONATION)

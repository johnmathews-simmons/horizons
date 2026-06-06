"""``POST /v1/auth/{login,refresh,logout}``.

The three auth-flow endpoints, all on one router. Login is the only
unauthenticated entry point; refresh and logout depend on a valid
refresh-kind JWT presented either as a cookie (browser) or as a bearer
header (programmatic).

Response-shape contract (see ``docs/api/auth.md`` for the prose):

- **Login** picks the shape from the client-controlled
  ``X-Client-Type: browser`` header. Login has no prior context the
  server can trust, so an explicit opt-in is the only available
  signal; mis-signalling at login risks at most echoing tokens the
  caller just produced through their own credentials.
- **Refresh / logout** pick the shape from the *token source* — cookie
  or Authorization header — recorded by ``require_refresh_principal``.
  ``X-Client-Type`` is **ignored** on these endpoints. Letting it
  drive the shape here would allow XSS-driven JS to call
  ``fetch('/v1/auth/refresh')`` (the browser attaches the ``HttpOnly``
  cookie automatically) without the header and coerce the server into
  returning the rotated refresh token in JSON, defeating ``HttpOnly``.
  Same exposure applies to logout's clearing cookie.

All three responses carry ``Cache-Control: private, no-store`` because
the body contains tokens that must not be cached anywhere.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from horizons_core.core.auth import (
    Principal,
    TokenKind,
    TokenProvider,
    hash_password,
    verify_password,
)
from horizons_core.db.session import bind_app_user_id
from horizons_core.repos.refresh_tokens import RefreshTokensRepository
from horizons_core.repos.users import UsersRepository
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from horizons_api.deps import (
    REFRESH_COOKIE_NAME,
    get_token_provider,
    login_session_dep,
    require_refresh_principal,
    session_for_refresh,
)
from horizons_api.deps.refresh import RefreshTokenSource

router = APIRouter(prefix="/v1/auth", tags=["auth"])


# ---- response models ---------------------------------------------------------


class LoginRequest(BaseModel):
    """Request body for ``POST /v1/auth/login``."""

    model_config = ConfigDict(frozen=True)

    email: EmailStr
    password: str


class TokenPair(BaseModel):
    """``{access_token, refresh_token}`` — the programmatic-client shape.

    ``refresh_token`` is optional because browser-shaped responses omit
    it (the cookie carries it). The same model is used for both shapes
    so the route returns one type and the docs stay aligned.
    """

    model_config = ConfigDict(frozen=True)

    access_token: str
    refresh_token: str | None = None


# ---- timing-equalising sentinel hash -----------------------------------------

# Argon2-id verify burns ~100 ms by design. The "user not found" branch
# would otherwise return immediately, so an attacker could probe emails
# and read account existence from the response-time histogram. Always
# verify against a fixed sentinel hash on the missing-user branch so
# both branches consume the same CPU budget. The plaintext used to mint
# this hash is a fresh random token; nothing in the system knows it, so
# it can never match a real password.
_TIMING_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


# ---- helpers -----------------------------------------------------------------


_REFRESH_COOKIE_PATH = "/v1/auth"


def _is_browser_login(client_type: str | None) -> bool:
    """``True`` iff the *login* call opts into the browser shape.

    Login is the only flow that consults ``X-Client-Type``; refresh and
    logout derive the shape from the token source instead (see module
    docstring).
    """
    return client_type is not None and client_type.strip().lower() == "browser"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _set_refresh_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=max_age_seconds,
        path=_REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        # SameSite=None is required because the deployed SPA and API live
        # on different sites (SPA on Front Door / Storage `$web`; API on
        # the Container Apps default host). Under SameSite=Lax the browser
        # withholds this cookie on cross-site XHR — breaking /v1/auth/refresh
        # (cold-bootstrap on reload) and /v1/auth/logout. Secure + HttpOnly
        # remain on; CSRF risk is bounded by the cookie being read only by
        # require_refresh_principal on three explicit POST endpoints.
        samesite="none",
    )


def _clear_refresh_cookie(response: Response) -> None:
    # Setting Max-Age=0 with the same name + path tells the browser to
    # forget the cookie. Re-stating Secure / HttpOnly / SameSite isn't
    # strictly required by the spec for clearing but matches the cookie
    # we set on login, which keeps wire shape consistent.
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value="",
        max_age=0,
        path=_REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="none",
    )


async def _issue_pair_and_shape(
    *,
    provider: TokenProvider,
    session: AsyncSession,
    user_id: str | object,
    role: str,
    response: Response,
    is_browser: bool,
) -> TokenPair:
    """Mint access + refresh tokens and shape the response.

    Browser flow: set the cookie, omit ``refresh_token`` from the body.
    Programmatic flow: include ``refresh_token`` in the body, leave
    cookies alone.

    Both shapes carry ``Cache-Control: private, no-store``.
    """
    import uuid as _uuid

    if isinstance(user_id, str):
        uid = _uuid.UUID(user_id)
    elif isinstance(user_id, _uuid.UUID):
        uid = user_id
    else:  # pragma: no cover — narrowing fallback
        raise TypeError(f"unsupported user_id type {type(user_id)!r}")

    access = await provider.issue_token(user_id=uid, role=role, kind=TokenKind.ACCESS)
    refresh = await provider.issue_token(
        user_id=uid,
        role=role,
        kind=TokenKind.REFRESH,
        session=session,
    )

    _no_store(response)

    if is_browser:
        # 30 days, matching LocalJwtProvider's default REFRESH TTL.
        _set_refresh_cookie(response, refresh, max_age_seconds=30 * 24 * 60 * 60)
        return TokenPair(access_token=access, refresh_token=None)
    return TokenPair(access_token=access, refresh_token=refresh)


# ---- routes ------------------------------------------------------------------


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(login_session_dep)],
    provider: Annotated[TokenProvider, Depends(get_token_provider)],
    x_client_type: Annotated[str | None, Header()] = None,
) -> TokenPair:
    """Exchange email + password for an access / refresh token pair."""
    user = await UsersRepository(session).find_by_email(body.email)

    if user is None:
        # Constant-time defence against account-enumeration via response
        # timing. Run argon2 verify against a fixed sentinel hash so the
        # missing-user branch consumes the same CPU budget as a real
        # wrong-password verify. The result is discarded.
        verify_password(plaintext=body.password, password_hash=_TIMING_DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    if not verify_password(plaintext=body.password, password_hash=user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    # Bind the GUC inside the same transaction so the refresh-token
    # insert below satisfies refresh_tokens_owner_insert WITH CHECK
    # (user_id = current_setting('app.user_id')::uuid).
    await bind_app_user_id(session, user.id)

    return await _issue_pair_and_shape(
        provider=provider,
        session=session,
        user_id=user.id,
        role=user.role.value,
        response=response,
        is_browser=_is_browser_login(x_client_type),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    response: Response,
    verified: Annotated[
        tuple[Principal, RefreshTokenSource],
        Depends(require_refresh_principal),
    ],
    session: Annotated[AsyncSession, Depends(session_for_refresh)],
    provider: Annotated[TokenProvider, Depends(get_token_provider)],
) -> TokenPair:
    """Rotate the refresh token; mint a fresh access / refresh pair.

    Liveness check + revoke happen here (not in the dep) so the dep
    stays pure-crypto. A token whose ``jti`` is absent from
    ``refresh_tokens`` or already revoked is rejected with the uniform
    401.

    Two security points worth being explicit about:

    1. Response shape is bound to the token *source* (cookie vs
       header), not to ``X-Client-Type``. See module docstring.
    2. The caller's *current* role is re-read from ``users`` before
       issuing the new pair. Refresh is the boundary at which a role
       demotion (admin → client) or account removal takes effect; the
       stale claim in the refresh token is ignored. A missing user
       row returns 401 even though the refresh's signature was valid.
    """
    principal, source = verified
    repo = RefreshTokensRepository(session)
    revoked = await repo.revoke(
        jti=principal.jti,
        user_id=principal.user_id,
        revoked_at=datetime.now(UTC),
    )
    if not revoked:
        # ``revoke`` returns False both when the row is missing (RLS or
        # absent) and when it was already revoked. Either way the token
        # is dead — same 401 body as a forged token.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
        )

    # Re-read the user to bind the freshest role. The token's ``role``
    # claim is from issuance time and may be stale by minutes-to-days.
    user = await UsersRepository(session).get_by_id(principal.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
        )

    return await _issue_pair_and_shape(
        provider=provider,
        session=session,
        user_id=user.id,
        role=user.role.value,
        response=response,
        is_browser=source is RefreshTokenSource.COOKIE,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    verified: Annotated[
        tuple[Principal, RefreshTokenSource],
        Depends(require_refresh_principal),
    ],
    session: Annotated[AsyncSession, Depends(session_for_refresh)],
) -> Response:
    """Revoke the active refresh token; clear the cookie when it was the source."""
    principal, source = verified
    repo = RefreshTokensRepository(session)
    revoked = await repo.revoke(
        jti=principal.jti,
        user_id=principal.user_id,
        revoked_at=datetime.now(UTC),
    )
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
        )

    _no_store(response)
    if source is RefreshTokenSource.COOKIE:
        # Cookie was the source → clear it. ``X-Client-Type`` is
        # deliberately ignored here, see module docstring.
        _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response

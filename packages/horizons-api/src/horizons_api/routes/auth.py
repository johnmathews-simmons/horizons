"""``POST /v1/auth/{login,refresh,logout}``.

The three auth-flow endpoints, all on one router. Login is the only
unauthenticated entry point; refresh and logout depend on a valid
refresh-kind JWT presented either as a cookie (browser) or as a bearer
header (programmatic).

The browser / programmatic distinction is signalled by an explicit
``X-Client-Type: browser`` header — see ``docs/api/auth.md`` for the
contract. Browser-shaped responses set / clear the
``HttpOnly; Secure; SameSite=Lax; Path=/v1/auth`` cookie and omit the
refresh token from the JSON body; programmatic-shaped responses return
both tokens in JSON and never touch cookies.

All three responses carry ``Cache-Control: private, no-store`` because
the body contains tokens that must not be cached anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from horizons_core.core.auth import (
    Principal,
    TokenKind,
    TokenProvider,
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


# ---- helpers -----------------------------------------------------------------


_REFRESH_COOKIE_PATH = "/v1/auth"


def _is_browser_client(client_type: str | None) -> bool:
    """``True`` iff the call is shaped for the browser flow.

    Anything other than the literal ``browser`` value (including a
    missing header, capitalisation variants, or extra whitespace after
    stripping) is treated as programmatic. The header is the
    *opt-in*; defaulting to programmatic keeps reverse-proxy
    misconfiguration from accidentally turning a programmatic client's
    response into a cookie-bearing one.
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
        samesite="lax",
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
        samesite="lax",
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
    if user is None or not verify_password(
        plaintext=body.password, password_hash=user.password_hash
    ):
        # Same body for both branches so the response cannot be used to
        # enumerate accounts.
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
        is_browser=_is_browser_client(x_client_type),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    response: Response,
    principal: Annotated[Principal, Depends(require_refresh_principal)],
    session: Annotated[AsyncSession, Depends(session_for_refresh)],
    provider: Annotated[TokenProvider, Depends(get_token_provider)],
    x_client_type: Annotated[str | None, Header()] = None,
) -> TokenPair:
    """Rotate the refresh token; mint a fresh access / refresh pair.

    Liveness check + revoke happen here (not in the dep) so the dep
    stays pure-crypto. A token whose ``jti`` is absent from
    ``refresh_tokens`` or already revoked is rejected with the uniform
    401.
    """
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

    return await _issue_pair_and_shape(
        provider=provider,
        session=session,
        user_id=principal.user_id,
        role=principal.role,
        response=response,
        is_browser=_is_browser_client(x_client_type),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    principal: Annotated[Principal, Depends(require_refresh_principal)],
    session: Annotated[AsyncSession, Depends(session_for_refresh)],
    x_client_type: Annotated[str | None, Header()] = None,
) -> Response:
    """Revoke the active refresh token; clear the cookie if browser."""
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
    if _is_browser_client(x_client_type):
        _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response

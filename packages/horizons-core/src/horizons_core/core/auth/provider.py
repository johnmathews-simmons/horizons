"""The ``TokenProvider`` Protocol and its supporting types.

Pluggable seam between the API layer and whatever issues / verifies
bearer tokens. ``LocalJwtProvider`` (``core.auth.local_jwt``) is the
demo-time implementation; an ``EntraIdProvider`` will sit alongside it
post-demo when we add SSO. The API and middleware import the Protocol
and the implementation is wired in at app construction time.

A ``Principal`` is the verified output of a token decode — the subset
of JWT claims the rest of the app reasons about. It deliberately
excludes alg / kid / sig fields; once the provider has validated those
they are not interesting downstream.

Token kinds are split into three (``access``, ``refresh``,
``impersonation``) because the middleware and the refresh endpoint
have different things to do with each. Access tokens authenticate
ordinary requests; refresh tokens are only valid against
``/v1/auth/refresh``; impersonation tokens are the audited admin
support-view carrier that WU4.5 will add. The ``kind`` claim is
checked at every authentication point so a refresh token cannot be
presented as a bearer to ``/v1/me``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class TokenKind(enum.StrEnum):
    """Mirrors the ``kind`` claim baked into every Horizons JWT."""

    ACCESS = "access"
    REFRESH = "refresh"
    IMPERSONATION = "impersonation"


@dataclass(frozen=True, slots=True)
class Principal:
    """The verified subject of a Horizons JWT.

    Carries the claims the rest of the app reasons about. The provider
    is responsible for refusing any token whose signature, algorithm,
    or expiry does not check out — by the time a Principal exists, the
    request is authenticated.
    """

    user_id: uuid.UUID
    role: str
    kind: TokenKind
    jti: uuid.UUID
    issued_at: datetime
    expires_at: datetime


class AuthError(Exception):
    """Base class for token issuance / verification failures."""


class InvalidTokenError(AuthError):
    """The token failed signature, claim, or expiry validation.

    Deliberately collapses signature mismatch / forged alg / wrong
    issuer / expired / not-yet-valid into one type — the middleware
    treats them all as 401 and the error body must not distinguish
    them (the distinction would leak verification internals).
    """


@runtime_checkable
class TokenProvider(Protocol):
    """The seam between the API surface and the token implementation.

    All three methods are async to keep the Protocol uniform across
    implementations: ``LocalJwtProvider`` writes a row on refresh
    issuance and reads / updates rows on revocation; a future
    ``EntraIdProvider`` will round-trip Microsoft Identity Platform.

    ``verify_token`` does **not** consult the database — access-token
    verification is hot-path and must stay pure-crypto. Refresh tokens
    are checked against ``refresh_tokens`` only inside the
    ``/v1/auth/refresh`` endpoint, after the JWT signature has been
    validated.
    """

    async def issue_token(
        self,
        *,
        user_id: uuid.UUID,
        role: str,
        kind: TokenKind,
        session: AsyncSession | None = None,
    ) -> str:
        """Mint and return a fresh bearer string.

        ``session`` is required when ``kind`` is ``REFRESH`` so the
        provider can persist the new row to ``refresh_tokens``. For
        ``ACCESS`` and ``IMPERSONATION`` tokens it must be ``None`` —
        passing one is a bug at the call site (refresh-only side
        effect on a non-refresh path).
        """
        ...

    def verify_token(self, token: str) -> Principal:
        """Decode + validate ``token``; return its Principal or raise.

        Synchronous because it does pure crypto / claim validation;
        the hot path through the middleware must not require a DB
        round-trip. Refresh-token revocation is checked separately by
        the refresh endpoint.
        """
        ...

    async def revoke_token(
        self,
        jti: uuid.UUID,
        *,
        user_id: uuid.UUID,
        session: AsyncSession,
    ) -> bool:
        """Mark a refresh token revoked; return whether the state changed.

        ``user_id`` is mandatory and must match the row's owner — the
        RLS policy enforces the same equality but the explicit
        argument keeps the ownership claim visible at the call site.
        Returns ``False`` if the row is out of scope, absent, or
        already revoked.
        """
        ...

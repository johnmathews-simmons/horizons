"""``LocalJwtProvider`` — RS256-signed JWTs over PyJWT.

The demo-time ``TokenProvider``. Keys are RSA PEM bytes passed at
construction (production wiring reads them from Azure Key Vault via
the IaC layer; tests generate ephemeral keypairs in a fixture).

The provider deliberately pins the algorithm. PyJWT's ``decode`` will
honour whatever the token's ``alg`` header claims unless the caller
restricts it explicitly — the historical ``alg=none`` and HS-vs-RSA
confusion attacks both rely on the verifier trusting the header. Here
the algorithm list is fixed at construction and PyJWT raises
``InvalidAlgorithmError`` on any mismatch.

Refresh tokens are persisted to ``refresh_tokens`` at issuance so the
``/v1/auth/refresh`` endpoint can confirm liveness and so logout can
revoke. The persistence happens inside the caller's session bracket —
the provider does not own session lifetime.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import jwt

from horizons_core.core.auth.provider import (
    InvalidTokenError,
    Principal,
    TokenKind,
)
from horizons_core.repos.refresh_tokens import RefreshTokensRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_DEFAULT_TTLS: Final[dict[TokenKind, timedelta]] = {
    TokenKind.ACCESS: timedelta(minutes=15),
    TokenKind.REFRESH: timedelta(days=30),
    TokenKind.IMPERSONATION: timedelta(minutes=15),
}

_DEFAULT_LEEWAY: Final[timedelta] = timedelta(seconds=30)


class LocalJwtProvider:
    """RS256 JWT issuer / verifier with a Postgres-backed refresh registry.

    ``algorithm`` is pinned at construction; the verifier passes the
    one-element list to PyJWT so an attacker cannot downgrade to a
    different family (the classic HS-with-RSA-public-key attack) or
    to ``alg=none``.
    """

    def __init__(
        self,
        *,
        private_key: bytes | str,
        public_key: bytes | str,
        issuer: str,
        audience: str,
        algorithm: str = "RS256",
        ttls: dict[TokenKind, timedelta] | None = None,
        leeway: timedelta = _DEFAULT_LEEWAY,
    ) -> None:
        if algorithm in {"none", "HS256", "HS384", "HS512"}:
            # The Protocol allows any algorithm but the local provider
            # is RSA-only; HS* would mean the verification key is the
            # signing key, which makes the JWK distribution model
            # different. Reject at construction so a misconfiguration
            # fails fast and obviously instead of weakening the seam.
            raise ValueError(
                f"LocalJwtProvider requires an RSA algorithm; got {algorithm!r}"
            )
        self._private_key = private_key
        self._public_key = public_key
        self._issuer = issuer
        self._audience = audience
        self._algorithm = algorithm
        self._ttls = dict(_DEFAULT_TTLS) | (ttls or {})
        self._leeway = leeway

    async def issue_token(
        self,
        *,
        user_id: uuid.UUID,
        role: str,
        kind: TokenKind,
        session: AsyncSession | None = None,
    ) -> str:
        if kind is TokenKind.REFRESH and session is None:
            raise ValueError(
                "issue_token requires a session when kind=REFRESH "
                "(the row must be recorded in refresh_tokens)"
            )
        if kind is not TokenKind.REFRESH and session is not None:
            raise ValueError(
                "issue_token must not be passed a session for non-REFRESH "
                "kinds — there is no row to write"
            )

        now = datetime.now(UTC)
        jti = uuid.uuid4()
        expires_at = now + self._ttls[kind]
        payload: dict[str, Any] = {
            "sub": str(user_id),
            "role": role,
            "kind": kind.value,
            "jti": str(jti),
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": self._issuer,
            "aud": self._audience,
        }
        token = jwt.encode(payload, self._private_key, algorithm=self._algorithm)

        if kind is TokenKind.REFRESH:
            # Narrowed for the type checker — the guard above raised if
            # session was None for a REFRESH kind.
            assert session is not None
            await RefreshTokensRepository(session).record(
                jti=jti,
                user_id=user_id,
                issued_at=now,
                expires_at=expires_at,
            )

        return token

    def verify_token(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={
                    "require": ["sub", "role", "kind", "jti", "iat", "exp", "iss", "aud"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(str(exc)) from exc

        try:
            user_id = uuid.UUID(claims["sub"])
            jti = uuid.UUID(claims["jti"])
            kind = TokenKind(claims["kind"])
            role = str(claims["role"])
            issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=UTC)
            expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
        except (KeyError, ValueError) as exc:
            raise InvalidTokenError(f"malformed claims: {exc}") from exc

        return Principal(
            user_id=user_id,
            role=role,
            kind=kind,
            jti=jti,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    async def revoke_token(
        self,
        jti: uuid.UUID,
        *,
        user_id: uuid.UUID,
        session: AsyncSession,
    ) -> bool:
        return await RefreshTokensRepository(session).revoke(
            jti=jti,
            user_id=user_id,
            revoked_at=datetime.now(UTC),
        )

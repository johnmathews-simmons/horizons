"""Unit tests for ``LocalJwtProvider``.

Covers the WU4.0 acceptance contract:

- Token forgery rejection (tampered signature).
- Algorithm pinning: ``alg=none`` is rejected; ``alg=HS256`` is
  rejected when the provider is configured for RS256, even when the
  attacker uses the RSA public key as the HMAC secret (the classic
  HS-with-RSA-public-key confusion attack).
- Expiry: a token past its ``exp`` is rejected.
- Clock skew: a token issued just-after "now" is accepted within the
  configured leeway and rejected outside it.

No database is involved here — these are pure crypto + claim
validation. Refresh-flow persistence (``record`` / ``revoke``) is
exercised in the integration suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from horizons_core.core.auth.local_jwt import LocalJwtProvider
from horizons_core.core.auth.provider import (
    InvalidTokenError,
    Principal,
    TokenKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable

ISSUER = "horizons-test"
AUDIENCE = "horizons-clients"


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[bytes, bytes]:
    """Ephemeral 2048-bit RSA keypair for the test module."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@pytest.fixture
def provider(rsa_keypair: tuple[bytes, bytes]) -> LocalJwtProvider:
    private, public = rsa_keypair
    return LocalJwtProvider(
        private_key=private,
        public_key=public,
        issuer=ISSUER,
        audience=AUDIENCE,
    )


@pytest.fixture
def make_provider(
    rsa_keypair: tuple[bytes, bytes],
) -> Callable[..., LocalJwtProvider]:
    """Factory so individual tests can override ttls / leeway / algorithm."""
    private, public = rsa_keypair

    def _build(**overrides: object) -> LocalJwtProvider:
        kwargs: dict[str, object] = {
            "private_key": private,
            "public_key": public,
            "issuer": ISSUER,
            "audience": AUDIENCE,
        }
        kwargs.update(overrides)
        return LocalJwtProvider(**kwargs)  # type: ignore[arg-type]

    return _build


async def test_issue_and_verify_access_token_round_trips(
    provider: LocalJwtProvider,
) -> None:
    """Mint an access token, decode it, get back a matching Principal."""
    user_id = uuid.uuid4()
    token = await provider.issue_token(
        user_id=user_id,
        role="client",
        kind=TokenKind.ACCESS,
    )
    principal = provider.verify_token(token)

    assert isinstance(principal, Principal)
    assert principal.user_id == user_id
    assert principal.role == "client"
    assert principal.kind is TokenKind.ACCESS
    # iat <= exp; exp roughly TTL away.
    assert principal.issued_at < principal.expires_at
    ttl = principal.expires_at - principal.issued_at
    assert timedelta(minutes=14) <= ttl <= timedelta(minutes=16)


async def test_forged_signature_is_rejected(provider: LocalJwtProvider) -> None:
    """Flipping a byte in the signature segment must fail verification."""
    token = await provider.issue_token(
        user_id=uuid.uuid4(),
        role="client",
        kind=TokenKind.ACCESS,
    )
    header, payload, sig = token.split(".")
    # Tamper the signature: replace the first character with something
    # different — keeps the b64url shape but breaks the signature.
    tampered_first = "A" if sig[0] != "A" else "B"
    tampered = ".".join([header, payload, tampered_first + sig[1:]])

    with pytest.raises(InvalidTokenError):
        provider.verify_token(tampered)


async def test_alg_none_is_rejected(provider: LocalJwtProvider) -> None:
    """An attacker-supplied ``alg=none`` token must be rejected.

    The vintage PyJWT footgun: if the verifier does not pin
    ``algorithms=``, ``alg=none`` strips the signature requirement.
    The provider passes a one-element ``[RS256]`` list, so PyJWT raises
    ``InvalidAlgorithmError`` before it ever looks at the signature.
    """
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "client",
        "kind": TokenKind.ACCESS.value,
        "jti": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    forged = jwt.encode(payload, "", algorithm="none")

    with pytest.raises(InvalidTokenError):
        provider.verify_token(forged)


async def test_alg_hs256_is_rejected_when_pinned_to_rs256(
    provider: LocalJwtProvider,
) -> None:
    """Any ``alg=HS256`` token must be refused, regardless of HMAC secret.

    The classical HS-with-RSA-public-key attack works against a
    verifier that does not pin ``algorithms=`` — the attacker would
    HMAC the payload using the RSA public key as the secret and the
    naive verifier would accept it. Modern PyJWT refuses to *encode*
    such a token (its prepare_key step recognises the PEM and raises)
    so the test forges the JWS manually with an arbitrary HMAC secret;
    the assertion is simply that the verifier rejects any HS256-tagged
    token, which is what closes the algorithm-confusion class.
    """
    import base64
    import hashlib
    import hmac
    import json

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "sub": str(uuid.uuid4()),
                "role": "client",
                "kind": TokenKind.ACCESS.value,
                "jti": str(uuid.uuid4()),
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
                "iss": ISSUER,
                "aud": AUDIENCE,
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    secret = b"attacker-chosen-secret"
    sig = _b64url(hmac.new(secret, signing_input, hashlib.sha256).digest())
    forged = f"{header}.{payload}.{sig}"

    with pytest.raises(InvalidTokenError):
        provider.verify_token(forged)


async def test_expired_token_is_rejected(
    make_provider: Callable[..., LocalJwtProvider],
) -> None:
    """A token whose ``exp`` is in the past must be rejected."""
    # TTL of effectively zero — the token is dead the moment it's minted.
    provider = make_provider(
        ttls={TokenKind.ACCESS: timedelta(seconds=-60)},
        leeway=timedelta(seconds=0),
    )
    token = await provider.issue_token(
        user_id=uuid.uuid4(),
        role="client",
        kind=TokenKind.ACCESS,
    )
    with pytest.raises(InvalidTokenError):
        provider.verify_token(token)


async def test_clock_skew_within_leeway_is_accepted(
    rsa_keypair: tuple[bytes, bytes],
) -> None:
    """A token minted by a slightly-fast clock is accepted within leeway.

    Simulates the producer's clock being 20s ahead of the verifier's:
    we issue with ``iat / exp`` offset into the future, then verify.
    """
    private, public = rsa_keypair
    leeway = timedelta(seconds=30)
    provider = LocalJwtProvider(
        private_key=private,
        public_key=public,
        issuer=ISSUER,
        audience=AUDIENCE,
        leeway=leeway,
    )
    now = datetime.now(UTC)
    iat = now + timedelta(seconds=20)
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "client",
        "kind": TokenKind.ACCESS.value,
        "jti": str(uuid.uuid4()),
        "iat": int(iat.timestamp()),
        "exp": int((iat + timedelta(minutes=15)).timestamp()),
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    token = jwt.encode(payload, private, algorithm="RS256")

    principal = provider.verify_token(token)
    assert principal.role == "client"


async def test_clock_skew_outside_leeway_is_rejected(
    rsa_keypair: tuple[bytes, bytes],
) -> None:
    """A token minted further than ``leeway`` in the future is rejected.

    Same shape as the previous test but with the producer 5 minutes
    ahead. ``iat`` claim is enforced (``verify_iat=True``), so
    PyJWT rejects it as ``ImmatureSignatureError``.
    """
    private, public = rsa_keypair
    provider = LocalJwtProvider(
        private_key=private,
        public_key=public,
        issuer=ISSUER,
        audience=AUDIENCE,
        leeway=timedelta(seconds=30),
    )
    now = datetime.now(UTC)
    iat = now + timedelta(minutes=5)
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "client",
        "kind": TokenKind.ACCESS.value,
        "jti": str(uuid.uuid4()),
        "iat": int(iat.timestamp()),
        "exp": int((iat + timedelta(minutes=15)).timestamp()),
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    token = jwt.encode(payload, private, algorithm="RS256")

    with pytest.raises(InvalidTokenError):
        provider.verify_token(token)


async def test_wrong_issuer_is_rejected(provider: LocalJwtProvider) -> None:
    """A token minted for a different issuer must be rejected."""
    private_pem = provider._private_key  # noqa: SLF001 — test reaches in
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "client",
        "kind": TokenKind.ACCESS.value,
        "jti": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "iss": "some-other-service",
        "aud": AUDIENCE,
    }
    token = jwt.encode(payload, private_pem, algorithm="RS256")
    with pytest.raises(InvalidTokenError):
        provider.verify_token(token)


async def test_wrong_audience_is_rejected(provider: LocalJwtProvider) -> None:
    """A token minted for a different audience must be rejected."""
    private_pem = provider._private_key  # noqa: SLF001 — test reaches in
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "client",
        "kind": TokenKind.ACCESS.value,
        "jti": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "iss": ISSUER,
        "aud": "some-other-audience",
    }
    token = jwt.encode(payload, private_pem, algorithm="RS256")
    with pytest.raises(InvalidTokenError):
        provider.verify_token(token)


async def test_missing_required_claim_is_rejected(
    rsa_keypair: tuple[bytes, bytes],
    provider: LocalJwtProvider,
) -> None:
    """A token missing one of the required claims (``role``) is rejected."""
    private, _ = rsa_keypair
    payload = {
        "sub": str(uuid.uuid4()),
        # role intentionally missing
        "kind": TokenKind.ACCESS.value,
        "jti": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    token = jwt.encode(payload, private, algorithm="RS256")
    with pytest.raises(InvalidTokenError):
        provider.verify_token(token)


async def test_malformed_sub_claim_raises_invalid_token(
    rsa_keypair: tuple[bytes, bytes],
    provider: LocalJwtProvider,
) -> None:
    """``sub`` that is not a valid UUID is rejected with InvalidTokenError."""
    private, _ = rsa_keypair
    payload = {
        "sub": "not-a-uuid",
        "role": "client",
        "kind": TokenKind.ACCESS.value,
        "jti": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    token = jwt.encode(payload, private, algorithm="RS256")
    with pytest.raises(InvalidTokenError):
        provider.verify_token(token)


async def test_rejects_construction_with_hs256(
    rsa_keypair: tuple[bytes, bytes],
) -> None:
    """The local provider refuses an HMAC algorithm at construction.

    The reason is documented in the constructor docstring: HS* would
    flip the verifier's key from "the JWK we publish" to "the signing
    secret", which is a different distribution model. Wiring an HS*
    LocalJwtProvider is almost certainly a misconfiguration; fail
    obviously instead of weakening the seam.
    """
    private, public = rsa_keypair
    for bad in ("none", "HS256", "HS384", "HS512"):
        with pytest.raises(ValueError, match="RSA"):
            LocalJwtProvider(
                private_key=private,
                public_key=public,
                issuer=ISSUER,
                audience=AUDIENCE,
                algorithm=bad,
            )


async def test_issue_refresh_without_session_raises(
    provider: LocalJwtProvider,
) -> None:
    """Refresh-token issuance requires a session for the registry write."""
    with pytest.raises(ValueError, match="session"):
        await provider.issue_token(
            user_id=uuid.uuid4(),
            role="client",
            kind=TokenKind.REFRESH,
        )


async def test_issue_access_with_session_raises(
    provider: LocalJwtProvider,
) -> None:
    """Passing a session for a non-REFRESH kind is a misuse — fail loudly."""

    class _Stub:
        pass

    with pytest.raises(ValueError, match="REFRESH"):
        await provider.issue_token(
            user_id=uuid.uuid4(),
            role="client",
            kind=TokenKind.ACCESS,
            session=_Stub(),  # type: ignore[arg-type]
        )

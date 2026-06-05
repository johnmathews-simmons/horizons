"""Integration tests for ``LocalJwtProvider``'s refresh-token surface.

The pure-crypto behaviour (forgery rejection, algorithm pinning,
expiry, clock skew, missing claims) lives in
``packages/horizons-core/tests/test_local_jwt.py``. This file covers
the bits that require Postgres:

- Refresh issuance writes a ``refresh_tokens`` row keyed on the JWT
  jti, with ``expires_at`` matching the JWT's ``exp``.
- ``revoke_token`` flips ``revoked_at`` on the row and is idempotent
  (a second revoke returns ``False``).
- A second user cannot revoke another user's refresh token — the RLS
  policy + the repo's owner-scoped UPDATE both refuse, returning
  ``False`` instead of raising.

The session bracket assumes ``api_app`` so the WU1.4 RLS policies are
the real fence; this mirrors the shape Track 4's FastAPI auth
endpoints will use.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from horizons_core.core.auth.local_jwt import LocalJwtProvider
from horizons_core.core.auth.provider import TokenKind
from horizons_core.db.session import make_engine, session_for_user
from horizons_core.repos.refresh_tokens import RefreshTokensRepository
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ISSUER = "horizons-test"
AUDIENCE = "horizons-clients"


@pytest.fixture
def migrated_db(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, str]]:
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    async_url = postgres_container.get_connection_url(driver="asyncpg")
    monkeypatch.setenv("HORIZONS_DB_URL", sync_url)
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    sync_engine = create_engine(sync_url, future=True)
    try:
        yield sync_engine, async_url
    finally:
        sync_engine.dispose()


@pytest_asyncio.fixture
async def async_engine(
    migrated_db: tuple[Engine, str],
) -> AsyncIterator[AsyncEngine]:
    _, async_url = migrated_db
    eng = make_engine(async_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[bytes, bytes]:
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


def _make_user(sync: Engine, email: str) -> uuid.UUID:
    with sync.begin() as conn:
        return conn.execute(
            sqlalchemy.text(
                "INSERT INTO users (email, password_hash, role) "
                "VALUES (:e, 'hash', 'client') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()


@pytest.mark.integration
async def test_issue_refresh_persists_row_keyed_on_jti(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
    provider: LocalJwtProvider,
) -> None:
    sync, _ = migrated_db
    user_id = _make_user(sync, f"rt_issue_{uuid.uuid4().hex[:8]}@example.test")

    async with session_for_user(async_engine, user_id) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        token = await provider.issue_token(
            user_id=user_id,
            role="client",
            kind=TokenKind.REFRESH,
            session=session,
        )

    principal = provider.verify_token(token)
    assert principal.kind is TokenKind.REFRESH
    assert principal.user_id == user_id

    async with session_for_user(async_engine, user_id) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        row = await RefreshTokensRepository(session).get_by_jti(principal.jti)

    assert row is not None
    assert row.jti == principal.jti
    assert row.user_id == user_id
    assert row.revoked_at is None
    # JWT exp is integer seconds; the persisted expires_at is the same
    # source. Allow a 1-second slack for the float→int conversion.
    assert abs((row.expires_at - principal.expires_at).total_seconds()) <= 1.0


@pytest.mark.integration
async def test_revoke_marks_row_revoked_and_is_idempotent(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
    provider: LocalJwtProvider,
) -> None:
    sync, _ = migrated_db
    user_id = _make_user(sync, f"rt_revoke_{uuid.uuid4().hex[:8]}@example.test")

    async with session_for_user(async_engine, user_id) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        token = await provider.issue_token(
            user_id=user_id,
            role="client",
            kind=TokenKind.REFRESH,
            session=session,
        )
    principal = provider.verify_token(token)

    async with session_for_user(async_engine, user_id) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        first = await provider.revoke_token(
            principal.jti, user_id=user_id, session=session
        )
    async with session_for_user(async_engine, user_id) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        second = await provider.revoke_token(
            principal.jti, user_id=user_id, session=session
        )
        row = await RefreshTokensRepository(session).get_by_jti(principal.jti)

    assert first is True
    assert second is False
    assert row is not None
    assert row.revoked_at is not None


@pytest.mark.integration
async def test_other_user_cannot_revoke_my_refresh_token(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
    provider: LocalJwtProvider,
) -> None:
    """RLS + the repo's owner predicate both refuse a cross-user revoke."""
    sync, _ = migrated_db
    suffix = uuid.uuid4().hex[:8]
    owner_id = _make_user(sync, f"rt_owner_{suffix}@example.test")
    attacker_id = _make_user(sync, f"rt_attacker_{suffix}@example.test")

    async with session_for_user(async_engine, owner_id) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        token = await provider.issue_token(
            user_id=owner_id,
            role="client",
            kind=TokenKind.REFRESH,
            session=session,
        )
    principal = provider.verify_token(token)

    async with session_for_user(async_engine, attacker_id) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        changed = await provider.revoke_token(
            principal.jti, user_id=attacker_id, session=session
        )

    async with session_for_user(async_engine, owner_id) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        row = await RefreshTokensRepository(session).get_by_jti(principal.jti)

    assert changed is False
    assert row is not None
    assert row.revoked_at is None, "owner's refresh token must still be live"


@pytest.mark.integration
async def test_revoke_unknown_jti_returns_false(
    migrated_db: tuple[Engine, str],
    async_engine: AsyncEngine,
    provider: LocalJwtProvider,
) -> None:
    sync, _ = migrated_db
    user_id = _make_user(sync, f"rt_unknown_{uuid.uuid4().hex[:8]}@example.test")
    unknown_jti = uuid.uuid4()

    async with session_for_user(async_engine, user_id) as session:
        await session.execute(sqlalchemy.text("SET LOCAL ROLE api_app"))
        changed = await provider.revoke_token(
            unknown_jti, user_id=user_id, session=session
        )

    assert changed is False

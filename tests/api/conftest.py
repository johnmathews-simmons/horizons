# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Shared fixtures for WU7.2 / WU7.4 admin-endpoint integration tests.

These tests share the same structural setup as
``tests/test_admin_subscription_endpoints.py``:

- A session-scoped migrated-Postgres engine that runs Alembic against
  the testcontainers PG 18 instance (provided by ``tests/conftest.py``).
- An RSA keypair per test for the local JWT provider so
  ``/v1/auth/login`` issues real, verifiable bearer tokens.
- A ``TestClient`` against a fresh ``create_app()`` per test, with
  per-test env overrides for the JWT keys, DB URL, and CORS origins.

The fixtures are duplicated here rather than imported from the
subscription test module because the latter is a top-level test file
that lives outside any package — relying on its fixtures would couple
the two suites in a way the harness doesn't make easy.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from horizons_core.core.auth import hash_password
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

ISSUER = "horizons-api-wu7-test"
AUDIENCE = "horizons-clients-wu7-test"


@pytest.fixture(scope="session")
def migrated_postgres_h(postgres_container: PostgresContainer) -> Iterator[Engine]:
    """Session-scoped migrated Postgres for the admin health + audit suite.

    Distinct name from the subscription suite's ``migrated_postgres_a``
    so the two suites don't fight over ``HORIZONS_DB_URL`` setup /
    teardown ordering when run together.
    """
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    cfg = Config(str(ALEMBIC_INI))

    prev = os.environ.get("HORIZONS_DB_URL")
    os.environ["HORIZONS_DB_URL"] = sync_url
    try:
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("HORIZONS_DB_URL", None)
        else:
            os.environ["HORIZONS_DB_URL"] = prev
    eng = create_engine(sync_url, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def rsa_pems() -> tuple[bytes, bytes]:
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
def configured_env(
    rsa_pems: tuple[bytes, bytes],
    migrated_postgres_h: Engine,
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[bytes, bytes]:
    _ = migrated_postgres_h
    private_pem, public_pem = rsa_pems
    monkeypatch.setenv("HORIZONS_JWT_PRIVATE_KEY_PEM", private_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_PUBLIC_KEY_PEM", public_pem.decode())
    monkeypatch.setenv("HORIZONS_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("HORIZONS_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("HORIZONS_CORS_ORIGINS", "")
    monkeypatch.setenv(
        "HORIZONS_DB_URL",
        postgres_container.get_connection_url(driver="asyncpg"),
    )
    # Force the Log Analytics workspace id to absent unless a specific
    # test overrides it via a fake client. Local-dev graceful
    # degradation is the default posture for the suite.
    monkeypatch.delenv("HORIZONS_LOG_ANALYTICS_WORKSPACE_ID", raising=False)

    from horizons_api.deps.provider import reset_provider_for_tests
    from horizons_core.db import session as session_mod
    from horizons_core.observability import health as health_mod

    reset_provider_for_tests()
    session_mod._engine = None  # type: ignore[attr-defined]  # noqa: SLF001
    health_mod.reset_logs_query_client_for_tests()
    return private_pem, public_pem


@pytest.fixture
def client(configured_env: tuple[bytes, bytes]) -> Iterator[TestClient]:
    _ = configured_env
    from horizons_api.app import create_app

    app = create_app()
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def make_user(engine: Engine, email: str, role: str = "client") -> uuid.UUID:
    """Insert a user row and return its id."""
    pw_hash = hash_password("pw")
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO users (email, password_hash, role) VALUES (:e, :p, :r) RETURNING id"),
            {"e": email, "p": pw_hash, "r": role},
        ).scalar_one()


def login(client: TestClient, email: str) -> str:
    """POST /v1/auth/login and return the access token."""
    response = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "pw"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}

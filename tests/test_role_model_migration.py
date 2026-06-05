"""Integration test for the WU1.0 role-model migration.

Applies the Alembic migration tree against a fresh Postgres 18 container
and asserts each of the four roles exists with the expected attributes.
The smoke test in this directory already confirms the testcontainers
substrate works; this test confirms the migration harness on top of it.

This test is intentionally sync: Alembic is a sync API and pytest-asyncio's
function-scoped event loop conflicts with the session-scoped async engine
fixture in ``conftest.py``. A short-lived sync SQLAlchemy engine for the
assertion queries sidesteps that entirely.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


EXPECTED_ROLES: dict[str, dict[str, bool]] = {
    "schema_owner": {
        "rolbypassrls": False,
        "rolcanlogin": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
    },
    "api_app": {
        "rolbypassrls": False,
        "rolcanlogin": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
    },
    "ingestion_worker": {
        "rolbypassrls": False,
        "rolcanlogin": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
    },
    "admin_bypass": {
        "rolbypassrls": True,
        "rolcanlogin": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
    },
}


@pytest.mark.integration
def test_role_model_migration_creates_all_four_roles(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_url = postgres_container.get_connection_url(driver="psycopg")
    monkeypatch.setenv("HORIZONS_DB_URL", sync_url)

    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")

    sync_engine = create_engine(sync_url, future=True)
    try:
        with sync_engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT rolname, rolbypassrls, rolcanlogin,
                           rolcreatedb, rolcreaterole
                    FROM pg_roles
                    WHERE rolname = ANY(:names)
                    ORDER BY rolname
                    """
                ),
                {"names": list(EXPECTED_ROLES.keys())},
            )
            observed = {
                row.rolname: {
                    "rolbypassrls": row.rolbypassrls,
                    "rolcanlogin": row.rolcanlogin,
                    "rolcreatedb": row.rolcreatedb,
                    "rolcreaterole": row.rolcreaterole,
                }
                for row in result
            }
    finally:
        sync_engine.dispose()

    assert observed == EXPECTED_ROLES

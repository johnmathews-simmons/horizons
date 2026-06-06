"""``admin_or_app_session`` switches role + audits for admin callers.

Mirrors the role-switch contract from ``deps/session.py``: client
callers run as ``api_app`` (RLS narrows), admin callers run as
``admin_bypass`` (BYPASSRLS) and write one ``admin_access_log`` row
per request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from horizons_api.deps.admin_or_app import admin_or_app_session
from horizons_core.db.models.admin_access_log import AdminAccessMode
from horizons_core.repos.admin_access_log import AdminAccessLogRepository
from sqlalchemy import text

if TYPE_CHECKING:
    from horizons_core.core.auth import Principal
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _consume(gen) -> AsyncSession:
    return await anext(gen)


async def test_client_principal_runs_as_api_app(client_principal: Principal) -> None:
    gen = admin_or_app_session(principal=client_principal, request_path="/v1/discovery")
    session = await _consume(gen)

    role = (await session.execute(text("SELECT current_user"))).scalar_one()
    assert role == "api_app"

    await gen.aclose()


async def test_admin_principal_runs_as_admin_bypass(admin_principal: Principal) -> None:
    gen = admin_or_app_session(principal=admin_principal, request_path="/v1/discovery")
    session = await _consume(gen)

    role = (await session.execute(text("SELECT current_user"))).scalar_one()
    assert role == "admin_bypass"

    await gen.aclose()


async def test_admin_request_writes_audit_row(
    admin_principal: Principal, pg_session_admin: AsyncSession
) -> None:
    before = len(
        await AdminAccessLogRepository(pg_session_admin).list_for_admin(admin_principal.user_id)
    )

    gen = admin_or_app_session(principal=admin_principal, request_path="/v1/temporal")
    await _consume(gen)
    await gen.aclose()

    after_rows = await AdminAccessLogRepository(pg_session_admin).list_for_admin(
        admin_principal.user_id
    )
    assert len(after_rows) == before + 1
    newest = after_rows[0]
    assert newest.mode == AdminAccessMode.OPERATOR
    assert newest.reason == "/v1/temporal"
    assert newest.target_user_id is None


async def test_client_request_writes_no_audit_row(
    client_principal: Principal,
    admin_principal: Principal,
    pg_session_admin: AsyncSession,
) -> None:
    before = len(
        await AdminAccessLogRepository(pg_session_admin).list_for_admin(admin_principal.user_id)
    )

    gen = admin_or_app_session(principal=client_principal, request_path="/v1/discovery")
    await _consume(gen)
    await gen.aclose()

    after = len(
        await AdminAccessLogRepository(pg_session_admin).list_for_admin(admin_principal.user_id)
    )
    assert after == before

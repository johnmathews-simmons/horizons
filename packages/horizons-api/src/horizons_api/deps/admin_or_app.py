"""``admin_or_app_session`` — per-request bracket aware of admin role.

For ``role='client'`` callers: identical to ``session_for_request``
(``api_app`` role, ``app.user_id`` bound to the principal). RLS
narrows visibility to the caller's subscription scope.

For ``role='admin'`` callers (non-impersonation only): assume
``admin_bypass`` so the session reads every tenant, AND write one
``admin_access_log`` row with ``mode=OPERATOR`` and ``reason=<path>``
in a sibling transaction. The audit row commits before the working
session is yielded — see ``core.auth.admin._record_audit_row`` for
the rationale.

Apply this dependency to public-primitive routes whose RLS-narrowed
results would be empty (or wrong) for an admin caller: ``/v1/discovery``,
``/v1/temporal``, ``/v1/differential``, ``/v1/me/overview``. Plain
``session_for_request`` stays correct everywhere else.

Structure
---------
``admin_or_app_session(principal, *, request_path)`` is the testable
async-generator bracket — call it directly in integration tests by
passing a ``Principal`` and a ``request_path`` string.

``admin_or_app_session_dep(request, principal)`` is the thin
FastAPI-shaped wrapper that forwards ``request.url.path`` into the
bracket. Routes declare a dependency on ``admin_or_app_session_dep``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from horizons_core.core.auth import Principal, Role
from horizons_core.core.auth.admin import _record_audit_row
from horizons_core.db.models.admin_access_log import AdminAccessMode
from horizons_core.db.session import (
    get_engine,
    get_session,
    session_for_user,
    set_local_role,
)

from horizons_api.deps.auth import authenticated_user

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


async def admin_or_app_session(
    principal: Principal,
    *,
    request_path: str,
) -> AsyncGenerator[AsyncSession]:
    """Yield a session that escalates to ``admin_bypass`` for admin callers.

    For ``Role.ADMIN`` principals: writes one ``admin_access_log`` row
    (mode=``OPERATOR``, reason=``request_path``) in its own committed
    transaction, then yields a working session bound to the admin's id
    under ``admin_bypass``. The audit row persists even if the caller's
    body raises and rolls the working session back.

    For ``Role.CLIENT`` principals: yields a session bound to
    ``principal.user_id`` under ``api_app``, identical to
    ``session_for_request``.
    """
    if principal.role == Role.ADMIN:
        engine = get_engine()
        token_id = uuid.uuid4()
        await _record_audit_row(
            engine,
            mode=AdminAccessMode.OPERATOR,
            admin_id=principal.user_id,
            target_user_id=None,
            token_id=token_id,
            reason=request_path,
        )
        async with session_for_user(engine, principal.user_id) as session:
            await set_local_role(session, "admin_bypass")
            yield session
        return

    async with get_session(principal.user_id) as session:
        await set_local_role(session, "api_app")
        yield session


async def admin_or_app_session_dep(
    request: Request,
    principal: Annotated[Principal, Depends(authenticated_user)],
) -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency: forwards ``request.url.path`` into the bracket.

    Declare this as the session dependency on routes that must return
    corpus-wide data for admin callers (``/v1/discovery``,
    ``/v1/temporal``, ``/v1/differential``, ``/v1/me/overview``).
    """
    async for session in admin_or_app_session(principal, request_path=request.url.path):
        yield session

"""``session_for_request`` — per-request Postgres session bracket.

Depends on ``authenticated_user`` so the bracket only opens for
authenticated requests (no DB hit on a 401). Inside the bracket:

- ``session_for_user(principal.user_id)`` binds ``app.user_id`` for
  the transaction (the GUC every RLS policy keys on).
- ``set_local_role(session, "api_app")`` switches the role so the
  RLS policies fire (``BYPASSRLS`` only attaches to ``admin_bypass``,
  never to the LOGIN user the API connects as).

The bracket commits on normal exit and rolls back on exception, per
the WU1.5 session contract.

``session_for_request_or_admin`` is the role-aware sibling used by
routes that should serve both clients and admins through a single
handler. Client principals get the standard ``api_app`` bracket above;
admin principals get an ``admin_operator_session`` bracket — which
writes one ``admin_access_log`` row and runs under ``admin_bypass`` so
the same query that returns scope-filtered rows for a client returns
the entire corpus for an admin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from horizons_core.core.auth import Principal
from horizons_core.core.auth.admin import admin_operator_session
from horizons_core.db.session import get_session, set_local_role

from horizons_api.deps.auth import authenticated_user

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


_ADMIN_ROLE = "admin"


async def session_for_request(
    principal: Annotated[Principal, Depends(authenticated_user)],
) -> AsyncGenerator[AsyncSession]:
    """Yield a session bound to ``principal.user_id`` under ``api_app``."""
    async with get_session(principal.user_id) as session:
        await set_local_role(session, "api_app")
        yield session


async def session_for_request_or_admin(
    principal: Annotated[Principal, Depends(authenticated_user)],
) -> AsyncGenerator[AsyncSession]:
    """Yield a session whose role matches the principal.

    Client principals get an ``api_app`` session (RLS-filtered);
    admin principals get an ``admin_operator_session`` (BYPASSRLS via
    role attribute, one ``admin_access_log`` row written before the
    session is yielded). Use this on routes whose handler logic is
    identical for both roles but whose visible rows differ — e.g. the
    documents browser, where the admin sees the entire corpus and a
    client sees their subscription scope.
    """
    if principal.role == _ADMIN_ROLE:
        async with admin_operator_session(principal.user_id) as session:
            yield session
        return
    async with get_session(principal.user_id) as session:
        await set_local_role(session, "api_app")
        yield session

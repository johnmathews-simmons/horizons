"""Admin-only dependencies.

``require_admin_principal`` runs after the access-token bearer dep and
asserts ``principal.role == 'admin'``. A non-admin caller gets **403**,
not 404 — the documented exception to the "404 not 403" rule we apply
on private-state endpoints. The reasoning:

- ``/v1/admin/*`` is a known-administrative URL prefix; concealing it
  with 404 buys nothing for an authenticated client who can read OpenAPI
  and already knows the prefix exists.
- Returning 403 surfaces "you are authenticated but not authorized" so
  the SPA's admin-route guard can render an explicit error rather than
  routing the user to a generic not-found page.

``admin_operator_session_for_request`` wraps ``admin_operator_session``
from ``horizons_core.core.auth.admin`` as a FastAPI dependency. It
yields an ``AsyncSession`` running under the ``admin_bypass`` role and
writes one ``admin_access_log`` row per request, before the route body
executes — same semantics as the WU1.9 context manager.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, status
from horizons_core.core.auth import Principal
from horizons_core.core.auth.admin import admin_operator_session

from horizons_api.deps.auth import authenticated_user

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


_ADMIN_ROLE = "admin"


def require_admin_principal(
    principal: Annotated[Principal, Depends(authenticated_user)],
) -> Principal:
    """Allow only ``role='admin'`` access-token bearers.

    Non-admin callers get a uniform 403 body. The status code is the
    explicit signal: ``/v1/admin/*`` is administrative, so 403 is more
    informative than the 404 we use on private-state endpoints.
    """
    if principal.role != _ADMIN_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return principal


async def admin_operator_session_for_request(
    principal: Annotated[Principal, Depends(require_admin_principal)],
) -> AsyncGenerator[AsyncSession]:
    """Yield an ``admin_bypass`` session + write one audit row.

    The ``admin_operator_session`` context manager from
    ``core.auth.admin`` does the heavy lifting: opens a short-lived
    audit-row write transaction, commits, then opens the working
    session bound to the admin's id and assumes ``admin_bypass``.

    The audit row's ``mode`` is ``operator`` and ``target_user_id`` is
    ``None``; that matches the BYPASSRLS semantics. Per-route ``reason``
    text could be wired through later; for the WU4.5 surface a blank
    reason keeps the audit row minimal.
    """
    async with admin_operator_session(principal.user_id) as session:
        yield session

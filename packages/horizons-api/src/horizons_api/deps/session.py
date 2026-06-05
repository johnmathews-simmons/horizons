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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from horizons_core.core.auth import Principal
from horizons_core.db.session import get_session, set_local_role

from horizons_api.deps.auth import authenticated_user

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


async def session_for_request(
    principal: Annotated[Principal, Depends(authenticated_user)],
) -> AsyncGenerator[AsyncSession]:
    """Yield a session bound to ``principal.user_id`` under ``api_app``."""
    async with get_session(principal.user_id) as session:
        await set_local_role(session, "api_app")
        yield session

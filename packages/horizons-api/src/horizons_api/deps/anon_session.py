"""``login_session_dep`` — DB session for unauthenticated auth-flow calls.

Login does not yet know the caller's user id when it needs to read
``users`` by email. ``session_for_request`` (the standard authenticated
bracket) cannot serve that case because it depends on
``authenticated_user`` which opens an open question: there is no bearer
yet at login. This dep replaces the bracket for that narrow surface.

Inside the bracket:

- Role is set to ``api_app`` so the same grant matrix applies (the role
  has SELECT on ``users``; today there is no RLS on ``users`` to gate
  the lookup).
- ``app.user_id`` is intentionally **not** bound at session entry. The
  route binds it via ``horizons_core.db.session.bind_app_user_id`` once
  the user is identified, so that the subsequent refresh-token write
  satisfies the ``refresh_tokens_owner_insert`` ``WITH CHECK``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from horizons_core.db.session import get_unauthenticated_session, set_local_role

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


async def login_session_dep() -> AsyncGenerator[AsyncSession]:
    """Yield a session in role ``api_app`` with no ``app.user_id`` bound."""
    async with get_unauthenticated_session() as session:
        await set_local_role(session, "api_app")
        yield session

"""FastAPI dependency-injection surface for the API.

Four deps, layered:

- ``get_token_provider`` (``provider``) returns the process-wide
  ``TokenProvider``. Built once at app startup from ``ApiSettings``;
  tests override via ``app.dependency_overrides`` to inject an
  ephemeral keypair.

- ``require_kind(kind)`` (``auth``) is a factory that builds a
  bearer-token dependency restricted to a specific ``TokenKind``.
  Missing, invalid, or wrong-kind bearer all raise
  ``HTTPException(401)`` with a uniform body so the client cannot
  distinguish the failure reason from the response.

- ``authenticated_user`` is the convenience alias for the dominant
  ``TokenKind.ACCESS`` case. Refresh / impersonation routes build
  their own dep via ``require_kind`` so the kind expectation lives
  next to the route declaration.

- ``session_for_request`` (``session``) depends on
  ``authenticated_user``, opens a ``session_for_user`` bracket bound
  to ``principal.user_id``, assumes ``SET LOCAL ROLE api_app``, and
  yields the ``AsyncSession``. The bracket commits on normal exit and
  rolls back on exception — see ``db/rls.md`` §Session contract.
"""

from __future__ import annotations

from horizons_api.deps.anon_session import login_session_dep
from horizons_api.deps.auth import authenticated_user, require_kind
from horizons_api.deps.provider import get_token_provider
from horizons_api.deps.refresh import (
    REFRESH_COOKIE_NAME,
    require_refresh_principal,
    session_for_refresh,
)
from horizons_api.deps.session import session_for_request

__all__ = [
    "REFRESH_COOKIE_NAME",
    "authenticated_user",
    "get_token_provider",
    "login_session_dep",
    "require_kind",
    "require_refresh_principal",
    "session_for_refresh",
    "session_for_request",
]

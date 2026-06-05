"""FastAPI dependency-injection surface for the API.

Three deps, layered:

- ``get_token_provider`` (``provider``) returns the process-wide
  ``TokenProvider``. Built once at app startup from ``ApiSettings``;
  tests override via ``app.dependency_overrides`` to inject an
  ephemeral keypair.

- ``authenticated_user`` (``auth``) extracts the bearer, calls
  ``TokenProvider.verify_token``, returns a ``Principal``. Missing or
  invalid bearer raises ``HTTPException(401)``.

- ``session_for_request`` (``session``) depends on
  ``authenticated_user``, opens a ``session_for_user`` bracket bound
  to ``principal.user_id``, assumes ``SET LOCAL ROLE api_app``, and
  yields the ``AsyncSession``. The bracket commits on normal exit and
  rolls back on exception — see ``db/rls.md`` §Session contract.
"""

from __future__ import annotations

from horizons_api.deps.auth import authenticated_user
from horizons_api.deps.provider import get_token_provider
from horizons_api.deps.session import session_for_request

__all__ = [
    "authenticated_user",
    "get_token_provider",
    "session_for_request",
]

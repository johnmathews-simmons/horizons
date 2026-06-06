"""Authentication and authorisation primitives.

WU1.9 lands the admin operator + impersonation context managers; WU4.0
adds the ``TokenProvider`` seam and its local-JWT implementation plus
the argon2 password helpers.

``Role`` is a convenience re-export of ``UserRole`` from
``horizons_core.db.models.users`` so that code that reasons about
``principal.role`` can compare against named constants
(``Role.ADMIN``, ``Role.CLIENT``) rather than bare string literals.
The underlying ``Principal.role`` field remains a plain ``str``; the
enum comparison works because ``UserRole`` is a ``StrEnum``.
"""

from __future__ import annotations

from horizons_core.core.auth.admin import (
    admin_impersonation_session,
    admin_operator_session,
)
from horizons_core.core.auth.local_jwt import LocalJwtProvider
from horizons_core.core.auth.passwords import (
    hash_password,
    needs_rehash,
    verify_password,
)
from horizons_core.core.auth.provider import (
    AuthError,
    InvalidTokenError,
    Principal,
    TokenKind,
    TokenProvider,
)
from horizons_core.db.models.users import UserRole as Role

# ``verify_password`` is intentionally re-exported above; tests / callers
# typically reach for it via ``horizons_core.core.auth``.

__all__ = [
    "AuthError",
    "InvalidTokenError",
    "LocalJwtProvider",
    "Principal",
    "Role",
    "TokenKind",
    "TokenProvider",
    "admin_impersonation_session",
    "admin_operator_session",
    "hash_password",
    "needs_rehash",
    "verify_password",
]

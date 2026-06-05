"""Authentication and authorisation primitives.

WU1.9 lands the admin operator + impersonation context managers; Track 4
will add the matching token-mint and refresh seams.
"""

from __future__ import annotations

from horizons_core.core.auth.admin import (
    admin_impersonation_session,
    admin_operator_session,
)

__all__ = [
    "admin_impersonation_session",
    "admin_operator_session",
]

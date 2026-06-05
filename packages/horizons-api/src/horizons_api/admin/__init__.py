"""Admin-only HTTP surface.

Each module here exposes a FastAPI ``router`` mounted under the
``/v1/admin/`` prefix by ``horizons_api.app``. Every route depends on
``require_admin_principal`` so an authenticated-but-non-admin caller
gets a uniform 403 (the documented exception to the "404 not 403" rule
that the private-state surface uses).
"""

from __future__ import annotations

from horizons_api.admin import audit, clients, health, impersonate

__all__ = ["audit", "clients", "health", "impersonate"]

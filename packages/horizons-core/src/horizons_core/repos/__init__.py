"""Repository layer — defence-in-depth third layer on top of grants + RLS.

See ``repos.md`` for shape, ``user_id`` discipline, and the
ORM-only-no-text() rule.
"""

from __future__ import annotations

from horizons_core.repos.admin_access_log import (
    AdminAccessLogDTO,
    AdminAccessLogRepository,
)
from horizons_core.repos.base import Repository
from horizons_core.repos.clauses import ClauseDTO, ClausesRepository
from horizons_core.repos.documents import DocumentDTO, DocumentsRepository
from horizons_core.repos.refresh_tokens import RefreshTokenDTO, RefreshTokensRepository
from horizons_core.repos.users import UserDTO, UsersRepository
from horizons_core.repos.versions import (
    DocumentVersionDTO,
    DocumentVersionsRepository,
)
from horizons_core.repos.watchlists import WatchlistDTO, WatchlistsRepository

__all__ = [
    "AdminAccessLogDTO",
    "AdminAccessLogRepository",
    "ClauseDTO",
    "ClausesRepository",
    "DocumentDTO",
    "DocumentVersionDTO",
    "DocumentVersionsRepository",
    "DocumentsRepository",
    "RefreshTokenDTO",
    "RefreshTokensRepository",
    "Repository",
    "UserDTO",
    "UsersRepository",
    "WatchlistDTO",
    "WatchlistsRepository",
]

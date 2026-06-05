"""Declarative ORM models for the Horizons database.

Per-aggregate files (`users.py`, `subscriptions.py`, ...) each define
one or more `Base`-derived classes; this `__init__.py` re-exports them
alongside the shared declarative `Base` so callers have one import
surface.

``Base.metadata`` is what ``migrations/env.py`` hands to Alembic as
``target_metadata`` — autogenerate diffs this metadata against the
live database to produce the next migration.
"""

from __future__ import annotations

from horizons_core.db.models.admin_access_log import AdminAccessLog, AdminAccessMode
from horizons_core.db.models.base import Base
from horizons_core.db.models.clauses import Clause
from horizons_core.db.models.documents import Document
from horizons_core.db.models.subscriptions import Subscription, SubscriptionScope
from horizons_core.db.models.users import User, UserRole
from horizons_core.db.models.versions import DocumentVersion
from horizons_core.db.models.watchlists import Watchlist

__all__ = [
    "AdminAccessLog",
    "AdminAccessMode",
    "Base",
    "Clause",
    "Document",
    "DocumentVersion",
    "Subscription",
    "SubscriptionScope",
    "User",
    "UserRole",
    "Watchlist",
]

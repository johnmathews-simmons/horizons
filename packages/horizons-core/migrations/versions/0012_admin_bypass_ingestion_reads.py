"""Grant admin_bypass SELECT on the ingestion-side operator tables.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-05

WU7.2's ``/v1/admin/health/ingestion`` endpoint reads the current poll
backlog from ``document_poll_schedule`` and the recent-incidents window
from ``ingestion_incident``. Both tables were created in WU3.1 with
``ingestion_worker``-only grants because no consumer existed yet;
``admin_bypass`` cannot reach either today.

``BYPASSRLS`` bypasses row-level security but does not override
table-level GRANTs, so the admin health endpoint would fail with
``permission denied`` without these explicit grants.

The grants are read-only on purpose. Admin operators inspect the
ingestion-side queues; they do not mutate poll cadence or write
incident rows from this surface. WU3.x continues to be the only
write path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON document_poll_schedule TO admin_bypass;")
    op.execute("GRANT SELECT ON ingestion_incident TO admin_bypass;")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON ingestion_incident FROM admin_bypass;")
    op.execute("REVOKE SELECT ON document_poll_schedule FROM admin_bypass;")

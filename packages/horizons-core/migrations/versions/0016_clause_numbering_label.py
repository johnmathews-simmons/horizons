"""Add clauses.numbering_label — preserve the structural anchor for display.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-07

The parser already produces a ``numbering_label`` for every structural
clause (e.g. ``11.``, ``11A.``, ``(a)``, ``(i)``) and the slugified form
is captured in ``clause_path``, but the raw label was never persisted.
The continuous (flat) reader view in the webapp showed clause bodies
*without* their leading marker, so a clause whose only inter-version
difference was a structural rename (``11.`` → ``11A.``) read as
byte-identical even though the alignment pipeline correctly flagged it
as ``MOVED``. This migration adds a nullable column so the API can
surface the marker and the renderer can prefix it onto the body.

The column is nullable: tail leaves added via ``_add_leaf`` (loose
prose with no marker) carry NULL. Existing rows backfill to NULL — the
label isn't recoverable from history, so anything ingested before this
migration will continue to render without a marker until its next
version lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clauses",
        sa.Column("numbering_label", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clauses", "numbering_label")

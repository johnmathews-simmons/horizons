"""Add clauses.heading_text — preserve heading text for display.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-07

The parser already produces heading-anchored clauses with a separate
``heading_text`` field, but until now the persistence path only wrote
``body_text`` into ``clauses.text_content``. The display layer lost the
section titles entirely, even though ``clause_path`` retained their
slugified form. This migration adds a nullable ``heading_text`` column
so the API can surface them and the webapp can render section structure.

The column is nullable: leaf paragraphs with no heading carry NULL,
section-heading-only nodes (no direct body text) carry the heading text
with an empty ``text_content``. Existing rows backfill to NULL — the
heading text isn't recoverable from history, so anything ingested before
this migration will display body-only until its next version lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clauses",
        sa.Column("heading_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clauses", "heading_text")

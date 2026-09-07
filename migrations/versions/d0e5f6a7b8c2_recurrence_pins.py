"""recurring deadlines and pinned conversations

Revision ID: d0e5f6a7b8c2
Revises: c9d4e5f6a7b1
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d0e5f6a7b8c2"
down_revision: str | None = "c9d4e5f6a7b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("recurrence", sa.String(length=16), nullable=True))
    op.add_column("conversations", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "pinned_at")
    op.drop_column("tasks", "recurrence")

"""memory resurfacing: last_surfaced_at + surface_count (#21)

Revision ID: f8a3b4c9d0e1
Revises: e7f2a3b4c9d0
Create Date: 2026-09-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f8a3b4c9d0e1"
down_revision: str | None = "e7f2a3b4c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memories",
                  sa.Column("last_surfaced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memories",
                  sa.Column("surface_count", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("memories", "surface_count")
    op.drop_column("memories", "last_surfaced_at")

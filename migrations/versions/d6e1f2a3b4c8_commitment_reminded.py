"""commitment reminded_at (about-to-forget nudges)

Revision ID: d6e1f2a3b4c8
Revises: c5d0e1f2a3b7
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d6e1f2a3b4c8"
down_revision: str | None = "c5d0e1f2a3b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "commitments", sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("commitments", "reminded_at")

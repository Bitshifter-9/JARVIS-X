"""a checklist under a deadline

Revision ID: f2a7b8c9d0e4
Revises: e1f6a7b8c9d3
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f2a7b8c9d0e4"
down_revision: str | None = "e1f6a7b8c9d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("checklist", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("tasks", "checklist")

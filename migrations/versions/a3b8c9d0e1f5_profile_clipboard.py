"""synced clipboard on the profile

Revision ID: a3b8c9d0e1f5
Revises: f2a7b8c9d0e4
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a3b8c9d0e1f5"
down_revision: str | None = "f2a7b8c9d0e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("clipboard", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("profiles", "clipboard")

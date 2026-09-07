"""last known location per device

Revision ID: b4c9d0e1f2a6
Revises: a3b8c9d0e1f5
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b4c9d0e1f2a6"
down_revision: str | None = "a3b8c9d0e1f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("last_location", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "last_location")

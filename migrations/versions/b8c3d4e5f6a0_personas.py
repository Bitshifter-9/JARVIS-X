"""personas: a persona per conversation, user-defined personas on the profile

Revision ID: b8c3d4e5f6a0
Revises: a7b2c3d4e5f9
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8c3d4e5f6a0"
down_revision: str | None = "a7b2c3d4e5f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("persona", sa.String(length=64), nullable=True))
    op.add_column(
        "profiles",
        sa.Column(
            "personas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("profiles", "personas")
    op.drop_column("conversations", "persona")

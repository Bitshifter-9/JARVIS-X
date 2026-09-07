"""settings_overrides: settings saved from the app, shared by every process

Revision ID: d4e9a0b1c2f6
Revises: c3d8f1a2b4e5
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d4e9a0b1c2f6"
down_revision: str | None = "c3d8f1a2b4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings_overrides",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_settings_overrides")),
    )


def downgrade() -> None:
    op.drop_table("settings_overrides")

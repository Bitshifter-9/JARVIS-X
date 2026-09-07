"""pending_logins: Sign-in-with-Google attempts, in the database instead of a process dict

Revision ID: c3d8f1a2b4e5
Revises: 9a4e6c2d0b17
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d8f1a2b4e5"
down_revision: str | None = "9a4e6c2d0b17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_logins",
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("tokens", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.PrimaryKeyConstraint("state", name=op.f("pk_pending_logins")),
    )


def downgrade() -> None:
    op.drop_table("pending_logins")

"""commitments — promises the user made

Revision ID: c5d0e1f2a3b7
Revises: b4c9d0e1f2a6
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c5d0e1f2a3b7"
down_revision: str | None = "b4c9d0e1f2a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commitments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="chat"),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_commitments_user_id", "commitments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_commitments_user_id", table_name="commitments")
    op.drop_table("commitments")

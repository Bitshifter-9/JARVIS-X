"""mail insights: labels, spending, travel

Revision ID: e1f6a7b8c9d3
Revises: d0e5f6a7b8c2
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e1f6a7b8c9d3"
down_revision: str | None = "d0e5f6a7b8c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_insights",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("label", sa.String(length=32), nullable=False, server_default="Other"),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("merchant", sa.String(length=200), nullable=True),
        sa.Column("is_bill", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("travel", JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["source_objects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_id"),
    )
    op.create_index("ix_mail_insights_user_id", "mail_insights", ["user_id"])
    op.create_index("ix_mail_insights_occurred_at", "mail_insights", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_mail_insights_occurred_at", table_name="mail_insights")
    op.drop_index("ix_mail_insights_user_id", table_name="mail_insights")
    op.drop_table("mail_insights")

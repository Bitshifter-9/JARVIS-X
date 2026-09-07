"""decisions: decision journal + outcome review (#39)

Revision ID: a9b4c5d6e2f3
Revises: f8a3b4c9d0e1
Create Date: 2026-09-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a9b4c5d6e2f3"
down_revision: str | None = "f8a3b4c9d0e1"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="open", nullable=False),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name=op.f("fk_decisions_user_id_users"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decisions")),
    )
    op.create_index(op.f("ix_decisions_user_id"), "decisions", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_decisions_user_id"), table_name="decisions")
    op.drop_table("decisions")

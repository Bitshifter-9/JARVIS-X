"""second brain: worker heartbeats, connector sync results, profiles, chat feedback

Revision ID: f6a1c2d3e4b8
Revises: e5f0b1c2d3a7
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a1c2d3e4b8"
down_revision: str | None = "e5f0b1c2d3a7"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("last_tick_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_worker_heartbeats")),
    )
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("about", sa.Text(), nullable=False),
        sa.Column("priorities", sa.Text(), nullable=False),
        sa.Column("people", sa.Text(), nullable=False),
        sa.Column("style", sa.Text(), nullable=False),
        sa.Column("decisions", sa.Text(), nullable=False),
        sa.Column("learned_style", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_profiles_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_profiles")),
    )
    op.create_table(
        "chat_feedback",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_chat_feedback_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name=op.f("fk_chat_feedback_message_id_chat_messages"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_feedback")),
    )
    op.create_index(op.f("ix_chat_feedback_user_id"), "chat_feedback", ["user_id"], unique=False)
    op.add_column(
        "source_accounts",
        sa.Column("last_sync_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("source_accounts", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("source_accounts", "last_error")
    op.drop_column("source_accounts", "last_sync_result")
    op.drop_index(op.f("ix_chat_feedback_user_id"), table_name="chat_feedback")
    op.drop_table("chat_feedback")
    op.drop_table("profiles")
    op.drop_table("worker_heartbeats")

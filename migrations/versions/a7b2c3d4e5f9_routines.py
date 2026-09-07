"""routines

Revision ID: a7b2c3d4e5f9
Revises: f6a1c2d3e4b8
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b2c3d4e5f9"
down_revision: str | None = "f6a1c2d3e4b8"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "routines",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("cron", sa.String(length=64), nullable=True),
        sa.Column("trigger", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_routines_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routines")),
    )
    op.create_index(op.f("ix_routines_user_id"), "routines", ["user_id"], unique=False)
    op.create_index(op.f("ix_routines_next_run_at"), "routines", ["next_run_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_routines_next_run_at"), table_name="routines")
    op.drop_index(op.f("ix_routines_user_id"), table_name="routines")
    op.drop_table("routines")

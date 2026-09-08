"""health_samples: energy/health correlation (#38)

Revision ID: d3e7f8a9b5c6
Revises: c2d6e7f8a4b5
Create Date: 2026-09-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d3e7f8a9b5c6"
down_revision: str | None = "c2d6e7f8a4b5"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "health_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.DateTime(timezone=True), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=True),
        sa.Column("sleep_minutes", sa.Integer(), nullable=True),
        sa.Column("active_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name=op.f("fk_health_samples_user_id_users"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_health_samples")),
    )
    op.create_index(op.f("ix_health_samples_user_id"), "health_samples", ["user_id"])
    op.create_index("uq_health_samples_user_day", "health_samples",
                    ["user_id", "day"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_health_samples_user_day", table_name="health_samples")
    op.drop_index(op.f("ix_health_samples_user_id"), table_name="health_samples")
    op.drop_table("health_samples")

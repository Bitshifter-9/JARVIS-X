"""location_samples: coarse location trails (#5)

Revision ID: c2d6e7f8a4b5
Revises: b1c5d6e7f3a4
Create Date: 2026-09-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c2d6e7f8a4b5"
down_revision: str | None = "b1c5d6e7f3a4"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "location_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name=op.f("fk_location_samples_user_id_users"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_location_samples")),
    )
    op.create_index("ix_location_samples_user_at", "location_samples", ["user_id", "at"])
    op.create_index(op.f("ix_location_samples_user_id"), "location_samples", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_location_samples_user_id"), table_name="location_samples")
    op.drop_index("ix_location_samples_user_at", table_name="location_samples")
    op.drop_table("location_samples")

"""commitments: task_id (#44) + checked_in_at (#40)

Revision ID: b1c5d6e7f3a4
Revises: a9b4c5d6e2f3
Create Date: 2026-09-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b1c5d6e7f3a4"
down_revision: str | None = "a9b4c5d6e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("commitments", sa.Column("task_id", sa.Uuid(), nullable=True))
    op.add_column("commitments",
                  sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_commitments_task_id_tasks"), "commitments", "tasks",
        ["task_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_commitments_task_id_tasks"), "commitments", type_="foreignkey")
    op.drop_column("commitments", "checked_in_at")
    op.drop_column("commitments", "task_id")

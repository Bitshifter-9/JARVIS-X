"""reminder_mutes: disliked sender/channel suppression (#14)

Revision ID: e7f2a3b4c9d0
Revises: d6e1f2a3b4c8
Create Date: 2026-09-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e7f2a3b4c9d0"
down_revision: str | None = "d6e1f2a3b4c8"
branch_labels = None
depends_on = None

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "reminder_mutes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("signature", sa.String(length=360), nullable=False),
        sa.Column("label", sa.String(length=360), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("author", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name=op.f("fk_reminder_mutes_user_id_users"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminder_mutes")),
    )
    op.create_index(op.f("ix_reminder_mutes_user_id"), "reminder_mutes",
                    ["user_id"], unique=False)
    op.create_index("uq_reminder_mutes_user_sig", "reminder_mutes",
                    ["user_id", "signature"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_reminder_mutes_user_sig", table_name="reminder_mutes")
    op.drop_index(op.f("ix_reminder_mutes_user_id"), table_name="reminder_mutes")
    op.drop_table("reminder_mutes")

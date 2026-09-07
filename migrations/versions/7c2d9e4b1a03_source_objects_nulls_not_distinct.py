"""source_objects: NULLS NOT DISTINCT on the provider/account/object uniqueness

A Slack message, an OpenClaw event or a Canvas assignment has no ``source_accounts`` row,
so ``account_id`` is NULL — and Postgres's default uniqueness treats every NULL as
distinct. The upsert that keeps "one row per provider object" therefore inserted a fresh
duplicate on every redelivery for exactly the providers that push. NULLS NOT DISTINCT
(Postgres 15+) makes the index mean what the model always claimed.

Revision ID: 7c2d9e4b1a03
Revises: 3f1f74aa2d87
Create Date: 2026-09-06
"""

from __future__ import annotations

from alembic import op

revision: str = "7c2d9e4b1a03"
down_revision: str | None = "3f1f74aa2d87"
branch_labels = None
depends_on = None

INDEX = "uq_source_objects_provider_account_object"


def upgrade() -> None:
    # Collapse duplicates that the old index let through, keeping the newest row.
    op.execute(
        """
        DELETE FROM source_objects s
        USING source_objects t
        WHERE s.provider = t.provider
          AND s.object_id = t.object_id
          AND s.account_id IS NULL AND t.account_id IS NULL
          AND s.updated_at < t.updated_at
        """
    )
    op.drop_index(INDEX, table_name="source_objects")
    op.create_index(
        INDEX, "source_objects", ["provider", "account_id", "object_id"],
        unique=True, postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="source_objects")
    op.create_index(
        INDEX, "source_objects", ["provider", "account_id", "object_id"], unique=True
    )

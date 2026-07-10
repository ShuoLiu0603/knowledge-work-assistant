"""make memory jobs idempotent per user message

Revision ID: 20260710_0020
Revises: 20260710_0019
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260710_0020"
down_revision = "20260710_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "WITH ranked AS ("
        "SELECT id, ROW_NUMBER() OVER ("
        "PARTITION BY user_id, message_id ORDER BY created_at ASC, id ASC"
        ") AS row_number "
        "FROM user_memory_update_jobs WHERE message_id IS NOT NULL"
        ") "
        "UPDATE user_memory_update_jobs SET message_id = NULL "
        "WHERE id IN (SELECT id FROM ranked WHERE row_number > 1)"
    )
    op.create_index(
        "uq_user_memory_update_jobs_user_message_id",
        "user_memory_update_jobs",
        ["user_id", "message_id"],
        unique=True,
        postgresql_where=sa.text("message_id IS NOT NULL"),
        sqlite_where=sa.text("message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_memory_update_jobs_user_message_id",
        table_name="user_memory_update_jobs",
    )

"""add user memory update jobs

Revision ID: 20260707_0011
Revises: 20260707_0010
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260707_0011"
down_revision = "20260707_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_memory_update_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("message_id", sa.String(length=36), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("assistant_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_user_memory_update_jobs_user_id", "user_memory_update_jobs", ["user_id"])
    op.create_index("ix_user_memory_update_jobs_conversation_id", "user_memory_update_jobs", ["conversation_id"])
    op.create_index("ix_user_memory_update_jobs_message_id", "user_memory_update_jobs", ["message_id"])
    op.create_index("ix_user_memory_update_jobs_status", "user_memory_update_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_user_memory_update_jobs_status", table_name="user_memory_update_jobs")
    op.drop_index("ix_user_memory_update_jobs_message_id", table_name="user_memory_update_jobs")
    op.drop_index("ix_user_memory_update_jobs_conversation_id", table_name="user_memory_update_jobs")
    op.drop_index("ix_user_memory_update_jobs_user_id", table_name="user_memory_update_jobs")
    op.drop_table("user_memory_update_jobs")

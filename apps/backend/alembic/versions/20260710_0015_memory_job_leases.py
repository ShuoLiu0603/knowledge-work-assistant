"""add memory update job leases

Revision ID: 20260710_0015
Revises: 20260710_0014
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260710_0015"
down_revision = "20260710_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_memory_update_jobs",
        sa.Column("lease_token", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "user_memory_update_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_user_memory_update_jobs_status_lease_expires_at",
        "user_memory_update_jobs",
        ["status", "lease_expires_at"],
    )
    op.execute(
        "UPDATE user_memory_update_jobs "
        "SET status = 'queued', lease_token = '', lease_expires_at = NULL, "
        "error_message = 'worker dispatch failed: requeued during memory lease migration', "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE status = 'processing'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_memory_update_jobs_status_lease_expires_at",
        table_name="user_memory_update_jobs",
    )
    op.drop_column("user_memory_update_jobs", "lease_expires_at")
    op.drop_column("user_memory_update_jobs", "lease_token")

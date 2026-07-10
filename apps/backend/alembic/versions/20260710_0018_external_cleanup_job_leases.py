"""add external cleanup job leases

Revision ID: 20260710_0018
Revises: 20260710_0017
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260710_0018"
down_revision = "20260710_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "external_cleanup_jobs",
        sa.Column("lease_token", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "external_cleanup_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_external_cleanup_jobs_status_lease_expires_at",
        "external_cleanup_jobs",
        ["status", "lease_expires_at"],
    )
    op.execute(
        "UPDATE external_cleanup_jobs "
        "SET status = 'queued', lease_token = '', lease_expires_at = NULL, "
        "error_message = 'worker dispatch failed: requeued during cleanup lease migration', "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE status = 'processing'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_cleanup_jobs_status_lease_expires_at",
        table_name="external_cleanup_jobs",
    )
    op.drop_column("external_cleanup_jobs", "lease_expires_at")
    op.drop_column("external_cleanup_jobs", "lease_token")

"""add external cleanup jobs

Revision ID: 20260710_0013
Revises: 20260709_0012
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260710_0013"
down_revision = "20260709_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_cleanup_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("actor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False, server_default="delete_external_resources"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("object_keys", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_external_cleanup_jobs_actor_user_id", "external_cleanup_jobs", ["actor_user_id"])
    op.create_index("ix_external_cleanup_jobs_resource_type", "external_cleanup_jobs", ["resource_type"])
    op.create_index("ix_external_cleanup_jobs_resource_id", "external_cleanup_jobs", ["resource_id"])
    op.create_index("ix_external_cleanup_jobs_action", "external_cleanup_jobs", ["action"])
    op.create_index("ix_external_cleanup_jobs_status", "external_cleanup_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_external_cleanup_jobs_status", table_name="external_cleanup_jobs")
    op.drop_index("ix_external_cleanup_jobs_action", table_name="external_cleanup_jobs")
    op.drop_index("ix_external_cleanup_jobs_resource_id", table_name="external_cleanup_jobs")
    op.drop_index("ix_external_cleanup_jobs_resource_type", table_name="external_cleanup_jobs")
    op.drop_index("ix_external_cleanup_jobs_actor_user_id", table_name="external_cleanup_jobs")
    op.drop_table("external_cleanup_jobs")

"""track memory job dispatches

Revision ID: 20260710_0016
Revises: 20260710_0015
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260710_0016"
down_revision = "20260710_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_memory_update_jobs",
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_user_memory_update_jobs_status_dispatched_at",
        "user_memory_update_jobs",
        ["status", "dispatched_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_memory_update_jobs_status_dispatched_at",
        table_name="user_memory_update_jobs",
    )
    op.drop_column("user_memory_update_jobs", "dispatched_at")

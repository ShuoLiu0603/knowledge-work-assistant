"""add conversation summary cursor

Revision ID: 20260707_0008
Revises: 20260707_0007
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260707_0008"
down_revision = "20260707_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("summary_message_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("summary_message_count")

"""remove obsolete agent intent classification

Revision ID: 20260713_0024
Revises: 20260713_0023
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260713_0024"
down_revision = "20260713_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("agent_runs", "intent")


def downgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("intent", sa.String(length=30), nullable=False, server_default="rag"),
    )

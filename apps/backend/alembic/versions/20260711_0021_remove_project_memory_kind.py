"""merge project memories into profile

Revision ID: 20260711_0021
Revises: 20260710_0020
Create Date: 2026-07-11
"""

from __future__ import annotations

from alembic import op

revision = "20260711_0021"
down_revision = "20260710_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE user_memories "
        "SET kind = 'profile', memory_layer = 'profile', pinned = true, "
        "profile_slot = CASE WHEN profile_slot = '' THEN category ELSE profile_slot END "
        "WHERE kind = 'project'"
    )


def downgrade() -> None:
    # Project context cannot be distinguished reliably from other profile memories.
    pass

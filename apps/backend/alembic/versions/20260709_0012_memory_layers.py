"""add memory layer fields

Revision ID: 20260709_0012
Revises: 20260707_0011
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260709_0012"
down_revision = "20260707_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_memories") as batch:
        batch.add_column(sa.Column("canonical_key", sa.String(length=160), nullable=False, server_default=""))
        batch.add_column(sa.Column("memory_layer", sa.String(length=30), nullable=False, server_default="semantic"))
        batch.add_column(sa.Column("profile_slot", sa.String(length=80), nullable=False, server_default=""))
        batch.add_column(sa.Column("scope_type", sa.String(length=30), nullable=False, server_default="user"))
        batch.add_column(sa.Column("scope_id", sa.String(length=36), nullable=False, server_default=""))
        batch.add_column(sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_user_memories_canonical_key", ["canonical_key"])
        batch.create_index("ix_user_memories_memory_layer", ["memory_layer"])
        batch.create_index("ix_user_memories_profile_slot", ["profile_slot"])
        batch.create_index("ix_user_memories_scope_type", ["scope_type"])
        batch.create_index("ix_user_memories_scope_id", ["scope_id"])
        batch.create_index("ix_user_memories_expires_at", ["expires_at"])

    op.execute(
        """
        UPDATE user_memories
        SET memory_layer = CASE
            WHEN category IN ('response_detail', 'language', 'format')
                 OR kind IN ('profile', 'instruction')
            THEN 'profile'
            ELSE 'semantic'
        END
        """
    )
    op.execute(
        """
        UPDATE user_memories
        SET profile_slot = CASE
            WHEN category IN ('response_detail', 'language', 'format') THEN category
            WHEN kind IN ('profile', 'instruction') THEN COALESCE(NULLIF(category, ''), kind)
            ELSE ''
        END
        """
    )
    op.execute(
        """
        UPDATE user_memories
        SET pinned = CASE
            WHEN memory_layer = 'profile' THEN true
            ELSE false
        END
        """
    )
    op.execute("UPDATE user_memories SET scope_id = user_id WHERE scope_id = ''")
    op.execute(
        """
        UPDATE user_memories
        SET canonical_key = 'profile:' || profile_slot
        WHERE canonical_key = ''
          AND memory_layer = 'profile'
          AND profile_slot IN ('response_detail', 'language', 'format')
        """
    )
    op.execute(
        """
        WITH ranked_singletons AS (
            SELECT
                id,
                FIRST_VALUE(id) OVER (
                    PARTITION BY user_id, scope_type, scope_id, profile_slot
                    ORDER BY last_touched_at DESC, updated_at DESC, created_at DESC, id DESC
                ) AS winner_id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id, scope_type, scope_id, profile_slot
                    ORDER BY last_touched_at DESC, updated_at DESC, created_at DESC, id DESC
                ) AS row_number
            FROM user_memories
            WHERE status = 'active'
              AND memory_layer = 'profile'
              AND profile_slot IN ('response_detail', 'language', 'format')
        )
        UPDATE user_memories
        SET status = 'superseded',
            superseded_by_id = (
                SELECT winner_id
                FROM ranked_singletons
                WHERE ranked_singletons.id = user_memories.id
            ),
            invalid_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id
            FROM ranked_singletons
            WHERE row_number > 1
        )
        """
    )
    op.execute(
        """
        WITH ranked_canonical AS (
            SELECT
                id,
                FIRST_VALUE(id) OVER (
                    PARTITION BY user_id, scope_type, scope_id, canonical_key
                    ORDER BY last_touched_at DESC, updated_at DESC, created_at DESC, id DESC
                ) AS winner_id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id, scope_type, scope_id, canonical_key
                    ORDER BY last_touched_at DESC, updated_at DESC, created_at DESC, id DESC
                ) AS row_number
            FROM user_memories
            WHERE status = 'active'
              AND canonical_key <> ''
        )
        UPDATE user_memories
        SET status = 'superseded',
            superseded_by_id = (
                SELECT winner_id
                FROM ranked_canonical
                WHERE ranked_canonical.id = user_memories.id
            ),
            invalid_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT id
            FROM ranked_canonical
            WHERE row_number > 1
        )
        """
    )
    op.create_index(
        "uq_user_memories_active_profile_singleton",
        "user_memories",
        ["user_id", "scope_type", "scope_id", "profile_slot"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND memory_layer = 'profile' "
            "AND profile_slot IN ('response_detail', 'language', 'format')"
        ),
        sqlite_where=sa.text(
            "status = 'active' AND memory_layer = 'profile' "
            "AND profile_slot IN ('response_detail', 'language', 'format')"
        ),
    )
    op.create_index(
        "uq_user_memories_active_canonical_key",
        "user_memories",
        ["user_id", "scope_type", "scope_id", "canonical_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND canonical_key <> ''"),
        sqlite_where=sa.text("status = 'active' AND canonical_key <> ''"),
    )


def downgrade() -> None:
    with op.batch_alter_table("user_memories") as batch:
        batch.drop_index("uq_user_memories_active_canonical_key")
        batch.drop_index("uq_user_memories_active_profile_singleton")
        batch.drop_index("ix_user_memories_expires_at")
        batch.drop_index("ix_user_memories_scope_id")
        batch.drop_index("ix_user_memories_scope_type")
        batch.drop_index("ix_user_memories_profile_slot")
        batch.drop_index("ix_user_memories_memory_layer")
        batch.drop_index("ix_user_memories_canonical_key")
        batch.drop_column("expires_at")
        batch.drop_column("revision")
        batch.drop_column("pinned")
        batch.drop_column("scope_id")
        batch.drop_column("scope_type")
        batch.drop_column("profile_slot")
        batch.drop_column("memory_layer")
        batch.drop_column("canonical_key")

"""expand sticky memory slots

Revision ID: 20260710_0014
Revises: 20260710_0013
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260710_0014"
down_revision = "20260710_0013"
branch_labels = None
depends_on = None

PROFILE_SINGLETON_SLOTS = (
    "response_detail",
    "language",
    "format",
    "name",
    "company",
    "team",
    "current_role",
)
PROJECT_SINGLETON_SLOTS = (
    "current_project",
    "current_stack",
    "backend_framework",
    "frontend_framework",
)
PROFILE_MEMORY_CATEGORIES = PROFILE_SINGLETON_SLOTS + ("role", "profile", "background")
PROJECT_STICKY_CATEGORIES = PROJECT_SINGLETON_SLOTS + ("architecture", "tooling")
STICKY_MEMORY_CATEGORIES = PROFILE_MEMORY_CATEGORIES + PROJECT_STICKY_CATEGORIES
SINGLETON_MEMORY_SLOTS = PROFILE_SINGLETON_SLOTS + PROJECT_SINGLETON_SLOTS


def quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_index("uq_user_memories_active_profile_singleton", table_name="user_memories")
    op.execute(
        f"""
        UPDATE user_memories
        SET memory_layer = 'profile'
        WHERE category IN ({quoted(STICKY_MEMORY_CATEGORIES)})
           OR kind IN ('profile', 'instruction')
        """
    )
    op.execute(
        f"""
        UPDATE user_memories
        SET profile_slot = CASE
            WHEN category IN ({quoted(STICKY_MEMORY_CATEGORIES)}) THEN category
            WHEN kind IN ('profile', 'instruction') THEN COALESCE(NULLIF(category, ''), kind)
            ELSE profile_slot
        END
        WHERE profile_slot = ''
        """
    )
    op.execute("UPDATE user_memories SET scope_id = user_id WHERE scope_id = ''")
    op.execute(
        f"""
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
              AND profile_slot IN ({quoted(SINGLETON_MEMORY_SLOTS)})
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
        f"""
        UPDATE user_memories
        SET canonical_key = 'profile:' || profile_slot
        WHERE canonical_key = ''
          AND memory_layer = 'profile'
          AND profile_slot IN ({quoted(PROFILE_SINGLETON_SLOTS)})
        """
    )
    op.execute(
        f"""
        UPDATE user_memories
        SET canonical_key = 'project:' || profile_slot
        WHERE canonical_key = ''
          AND memory_layer = 'profile'
          AND profile_slot IN ({quoted(PROJECT_SINGLETON_SLOTS)})
        """
    )
    op.create_index(
        "uq_user_memories_active_profile_singleton",
        "user_memories",
        ["user_id", "scope_type", "scope_id", "profile_slot"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND memory_layer = 'profile' "
            f"AND profile_slot IN ({quoted(SINGLETON_MEMORY_SLOTS)})"
        ),
        sqlite_where=sa.text(
            "status = 'active' AND memory_layer = 'profile' "
            f"AND profile_slot IN ({quoted(SINGLETON_MEMORY_SLOTS)})"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_user_memories_active_profile_singleton", table_name="user_memories")
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

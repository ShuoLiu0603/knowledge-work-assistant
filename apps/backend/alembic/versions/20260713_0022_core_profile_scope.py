"""limit always-on memory to the core user profile

Revision ID: 20260713_0022
Revises: 20260711_0021
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260713_0022"
down_revision = "20260711_0021"
branch_labels = None
depends_on = None

CORE_PROFILE_SINGLETON_SLOTS = (
    "response_detail",
    "language",
    "format",
    "name",
    "preferred_address",
    "current_role",
    "tone",
    "accessibility",
)
CORE_PROFILE_CATEGORIES = CORE_PROFILE_SINGLETON_SLOTS + ("global_instruction",)
EPISODIC_MEMORY_CATEGORIES = ("decision", "event", "task")
PROCEDURAL_MEMORY_CATEGORIES = ("workflow", "task_instruction", "domain_rule")


def quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_index("uq_user_memories_active_profile_singleton", table_name="user_memories")
    op.execute("UPDATE user_memories SET kind = 'profile' WHERE kind = 'project'")
    op.execute(
        f"""
        UPDATE user_memories
        SET memory_layer = CASE
                WHEN category IN ({quoted(CORE_PROFILE_CATEGORIES)}) THEN 'profile'
                WHEN category IN ({quoted(EPISODIC_MEMORY_CATEGORIES)}) THEN 'episodic'
                WHEN kind = 'instruction'
                     OR category IN ({quoted(PROCEDURAL_MEMORY_CATEGORIES)})
                THEN 'procedural'
                ELSE 'semantic'
            END,
            profile_slot = CASE
                WHEN category IN ({quoted(CORE_PROFILE_SINGLETON_SLOTS)}) THEN category
                ELSE ''
            END,
            pinned = CASE
                WHEN category IN ({quoted(CORE_PROFILE_CATEGORIES)}) THEN true
                ELSE false
            END
        """
    )
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
              AND profile_slot IN ({quoted(CORE_PROFILE_SINGLETON_SLOTS)})
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
          AND profile_slot IN ({quoted(CORE_PROFILE_SINGLETON_SLOTS)})
        """
    )
    op.create_index(
        "uq_user_memories_active_profile_singleton",
        "user_memories",
        ["user_id", "scope_type", "scope_id", "profile_slot"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND memory_layer = 'profile' "
            f"AND profile_slot IN ({quoted(CORE_PROFILE_SINGLETON_SLOTS)})"
        ),
        sqlite_where=sa.text(
            "status = 'active' AND memory_layer = 'profile' "
            f"AND profile_slot IN ({quoted(CORE_PROFILE_SINGLETON_SLOTS)})"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_user_memories_active_profile_singleton", table_name="user_memories")
    legacy_slots = CORE_PROFILE_SINGLETON_SLOTS + (
        "company",
        "team",
        "current_project",
        "current_stack",
        "backend_framework",
        "frontend_framework",
    )
    op.create_index(
        "uq_user_memories_active_profile_singleton",
        "user_memories",
        ["user_id", "scope_type", "scope_id", "profile_slot"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND memory_layer = 'profile' "
            f"AND profile_slot IN ({quoted(legacy_slots)})"
        ),
        sqlite_where=sa.text(
            "status = 'active' AND memory_layer = 'profile' "
            f"AND profile_slot IN ({quoted(legacy_slots)})"
        ),
    )

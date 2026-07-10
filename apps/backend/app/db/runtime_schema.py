from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.reflection import Inspector

from app.db.base import Base

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
PROFILE_SINGLETON_SLOT_SQL = ", ".join(f"'{slot}'" for slot in PROFILE_SINGLETON_SLOTS)
PROJECT_SINGLETON_SLOT_SQL = ", ".join(f"'{slot}'" for slot in PROJECT_SINGLETON_SLOTS)
STICKY_MEMORY_CATEGORY_SQL = ", ".join(f"'{category}'" for category in STICKY_MEMORY_CATEGORIES)
SINGLETON_MEMORY_SLOT_SQL = ", ".join(f"'{slot}'" for slot in SINGLETON_MEMORY_SLOTS)


def ensure_runtime_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    dialect = engine.dialect.name

    Base.metadata.create_all(
        bind=engine,
        tables=[
            Base.metadata.tables["departments"],
            Base.metadata.tables["external_cleanup_jobs"],
            Base.metadata.tables["user_memory_events"],
            Base.metadata.tables["user_memory_recall_logs"],
            Base.metadata.tables["user_memory_update_jobs"],
        ],
    )
    add_column_if_missing(engine, inspector, "users", "department_id", "department_id VARCHAR(36)")
    add_column_if_missing(engine, inspector, "users", "security_level", "security_level INTEGER NOT NULL DEFAULT 1")
    add_column_if_missing(engine, inspector, "knowledge_bases", "department_id", "department_id VARCHAR(36)")
    add_column_if_missing(engine, inspector, "documents", "security_level", "security_level INTEGER NOT NULL DEFAULT 1")
    add_column_if_missing(engine, inspector, "conversations", "summary", "summary TEXT")
    add_column_if_missing(
        engine,
        inspector,
        "conversations",
        "summary_message_count",
        "summary_message_count INTEGER NOT NULL DEFAULT 0",
    )
    add_column_if_missing(engine, inspector, "conversations", "search_scope", "search_scope VARCHAR(30) NOT NULL DEFAULT 'single'")
    add_column_if_missing(engine, inspector, "conversations", "search_department_id", "search_department_id VARCHAR(36)")
    make_column_nullable_if_supported(engine, inspector, "conversations", "knowledge_base_id")
    make_column_nullable_if_supported(engine, inspector, "agent_runs", "knowledge_base_id")
    make_column_nullable_if_supported(engine, inspector, "retrieval_logs", "knowledge_base_id")

    boolean_type = "BOOLEAN" if dialect == "postgresql" else "INTEGER"
    boolean_false = "false" if dialect == "postgresql" else "0"
    boolean_true = "true" if dialect == "postgresql" else "1"
    add_column_if_missing(engine, inspector, "user_memories", "canonical_key", "canonical_key VARCHAR(160) NOT NULL DEFAULT ''")
    add_column_if_missing(engine, inspector, "user_memories", "memory_layer", "memory_layer VARCHAR(30) NOT NULL DEFAULT 'semantic'")
    add_column_if_missing(engine, inspector, "user_memories", "profile_slot", "profile_slot VARCHAR(80) NOT NULL DEFAULT ''")
    add_column_if_missing(engine, inspector, "user_memories", "scope_type", "scope_type VARCHAR(30) NOT NULL DEFAULT 'user'")
    add_column_if_missing(engine, inspector, "user_memories", "scope_id", "scope_id VARCHAR(36) NOT NULL DEFAULT ''")
    add_column_if_missing(engine, inspector, "user_memories", "pinned", f"pinned {boolean_type} NOT NULL DEFAULT {boolean_false}")
    add_column_if_missing(engine, inspector, "user_memories", "revision", "revision INTEGER NOT NULL DEFAULT 1")
    add_column_if_missing(engine, inspector, "user_memories", "expires_at", "expires_at TIMESTAMP")
    add_column_if_missing(
        engine,
        inspector,
        "user_memory_update_jobs",
        "lease_token",
        "lease_token VARCHAR(64) NOT NULL DEFAULT ''",
    )
    add_column_if_missing(
        engine,
        inspector,
        "user_memory_update_jobs",
        "lease_expires_at",
        "lease_expires_at TIMESTAMP",
    )
    add_column_if_missing(
        engine,
        inspector,
        "user_memory_update_jobs",
        "dispatched_at",
        "dispatched_at TIMESTAMP",
    )
    add_column_if_missing(
        engine,
        inspector,
        "external_cleanup_jobs",
        "lease_token",
        "lease_token VARCHAR(64) NOT NULL DEFAULT ''",
    )
    add_column_if_missing(
        engine,
        inspector,
        "external_cleanup_jobs",
        "lease_expires_at",
        "lease_expires_at TIMESTAMP",
    )
    create_index_if_missing(engine, inspector, "user_memories", "ix_user_memories_canonical_key", ["canonical_key"])
    create_index_if_missing(engine, inspector, "user_memories", "ix_user_memories_memory_layer", ["memory_layer"])
    create_index_if_missing(engine, inspector, "user_memories", "ix_user_memories_profile_slot", ["profile_slot"])
    create_index_if_missing(engine, inspector, "user_memories", "ix_user_memories_scope_type", ["scope_type"])
    create_index_if_missing(engine, inspector, "user_memories", "ix_user_memories_scope_id", ["scope_id"])
    create_index_if_missing(engine, inspector, "user_memories", "ix_user_memories_expires_at", ["expires_at"])
    create_index_if_missing(
        engine,
        inspector,
        "user_memory_update_jobs",
        "ix_user_memory_update_jobs_status_lease_expires_at",
        ["status", "lease_expires_at"],
    )
    create_index_if_missing(
        engine,
        inspector,
        "user_memory_update_jobs",
        "ix_user_memory_update_jobs_status_dispatched_at",
        ["status", "dispatched_at"],
    )
    create_index_if_missing(
        engine,
        inspector,
        "external_cleanup_jobs",
        "ix_external_cleanup_jobs_status_lease_expires_at",
        ["status", "lease_expires_at"],
    )

    json_type = "JSONB" if dialect == "postgresql" else "TEXT"
    add_column_if_missing(
        engine,
        inspector,
        "conversations",
        "searched_knowledge_base_ids",
        f"searched_knowledge_base_ids {json_type} NOT NULL DEFAULT '[]'",
    )
    add_column_if_missing(
        engine,
        inspector,
        "conversations",
        "history_provenance_complete",
        f"history_provenance_complete {boolean_type} NOT NULL DEFAULT {boolean_false}",
    )
    add_column_if_missing(engine, inspector, "messages", "agent_trace", f"agent_trace {json_type}")
    add_column_if_missing(engine, inspector, "messages", "token_usage", f"token_usage {json_type}")
    add_column_if_missing(
        engine,
        inspector,
        "messages",
        "memory_enabled",
        f"memory_enabled {boolean_type} NOT NULL DEFAULT {boolean_true}",
    )

    content_tsv_type = "TSVECTOR" if dialect == "postgresql" else "TEXT"
    add_column_if_missing(engine, inspector, "document_chunks", "content_tsv", f"content_tsv {content_tsv_type}")
    add_column_if_missing(engine, inspector, "document_chunks", "qdrant_point_id", "qdrant_point_id VARCHAR(36)")
    add_column_if_missing(
        engine,
        inspector,
        "document_chunks",
        "security_level",
        "security_level INTEGER NOT NULL DEFAULT 1",
    )
    add_column_if_missing(
        engine,
        inspector,
        "retrieval_logs",
        "scope_type",
        "scope_type VARCHAR(30) NOT NULL DEFAULT 'single'",
    )
    add_column_if_missing(
        engine,
        inspector,
        "retrieval_logs",
        "searched_knowledge_base_ids",
        f"searched_knowledge_base_ids {json_type}",
    )
    add_column_if_missing(
        engine,
        inspector,
        "retrieval_logs",
        "reranker_enabled",
        f"reranker_enabled {boolean_type} NOT NULL DEFAULT {boolean_false}",
    )

    with engine.begin() as connection:
        connection.execute(text("UPDATE document_chunks SET qdrant_point_id = id WHERE qdrant_point_id IS NULL"))
        connection.execute(
            text(
                f"""
                UPDATE user_memories
                SET memory_layer = CASE
                    WHEN category IN ({STICKY_MEMORY_CATEGORY_SQL})
                         OR kind IN ('profile', 'instruction')
                    THEN 'profile'
                    ELSE memory_layer
                END
                WHERE memory_layer = 'semantic'
                """
            )
        )
        connection.execute(
            text(
                f"""
                UPDATE user_memories
                SET profile_slot = CASE
                    WHEN category IN ({STICKY_MEMORY_CATEGORY_SQL}) THEN category
                    WHEN kind IN ('profile', 'instruction') THEN category
                    ELSE profile_slot
                END
                WHERE profile_slot = ''
                """
            )
        )
        connection.execute(text("UPDATE user_memories SET scope_id = user_id WHERE scope_id = ''"))
        connection.execute(
            text(
                """
                UPDATE user_memory_update_jobs
                SET status = 'queued',
                    lease_token = '',
                    lease_expires_at = NULL,
                    dispatched_at = NULL,
                    error_message = 'worker dispatch failed: stale processing lease recovered at startup',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'processing'
                  AND (lease_expires_at IS NULL OR lease_expires_at < CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                f"""
                UPDATE user_memories
                SET canonical_key = 'profile:' || profile_slot
                WHERE canonical_key = ''
                  AND memory_layer = 'profile'
                  AND profile_slot IN ({PROFILE_SINGLETON_SLOT_SQL})
                """
            )
        )
        connection.execute(
            text(
                f"""
                UPDATE user_memories
                SET canonical_key = 'project:' || profile_slot
                WHERE canonical_key = ''
                  AND memory_layer = 'profile'
                  AND profile_slot IN ({PROJECT_SINGLETON_SLOT_SQL})
                """
            )
        )
        pinned_true = "true" if dialect == "postgresql" else "1"
        pinned_false = "false" if dialect == "postgresql" else "0"
        connection.execute(
            text(
                f"UPDATE user_memories SET pinned = {pinned_true} "
                f"WHERE memory_layer = 'profile' AND pinned = {pinned_false}"
            )
        )
        connection.execute(
            text(
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
                      AND profile_slot IN ({SINGLETON_MEMORY_SLOT_SQL})
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
        )
        connection.execute(
            text(
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
        )
    create_profile_singleton_unique_index_if_supported(engine, inspector)
    create_active_canonical_key_unique_index_if_supported(engine, inspector)
    create_memory_job_message_unique_index_if_supported(engine, inspector)


def add_column_if_missing(
    engine: Engine,
    inspector: Inspector,
    table_name: str,
    column_name: str,
    ddl_fragment: str,
) -> None:
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in columns:
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl_fragment}"))


def create_index_if_missing(
    engine: Engine,
    inspector: Inspector,
    table_name: str,
    index_name: str,
    column_names: list[str],
) -> None:
    indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name in indexes:
        return
    columns = ", ".join(column_names)
    with engine.begin() as connection:
        connection.execute(text(f"CREATE INDEX {index_name} ON {table_name} ({columns})"))


def create_profile_singleton_unique_index_if_supported(engine: Engine, inspector: Inspector) -> None:
    if engine.dialect.name not in {"postgresql", "sqlite"}:
        return
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX IF EXISTS uq_user_memories_active_profile_singleton"))
        connection.execute(
            text(
                f"""
                CREATE UNIQUE INDEX uq_user_memories_active_profile_singleton
                ON user_memories (user_id, scope_type, scope_id, profile_slot)
                WHERE status = 'active'
                  AND memory_layer = 'profile'
                  AND profile_slot IN ({SINGLETON_MEMORY_SLOT_SQL})
                """
            )
        )


def create_active_canonical_key_unique_index_if_supported(engine: Engine, inspector: Inspector) -> None:
    index_name = "uq_user_memories_active_canonical_key"
    inspector = inspect(engine)
    indexes = {index["name"] for index in inspector.get_indexes("user_memories")}
    if index_name in indexes:
        return
    if engine.dialect.name not in {"postgresql", "sqlite"}:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX uq_user_memories_active_canonical_key
                ON user_memories (user_id, scope_type, scope_id, canonical_key)
                WHERE status = 'active'
                  AND canonical_key <> ''
                """
            )
        )


def create_memory_job_message_unique_index_if_supported(engine: Engine, inspector: Inspector) -> None:
    index_name = "uq_user_memory_update_jobs_user_message_id"
    inspector = inspect(engine)
    indexes = {index["name"] for index in inspector.get_indexes("user_memory_update_jobs")}
    if index_name in indexes or engine.dialect.name not in {"postgresql", "sqlite"}:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY user_id, message_id
                               ORDER BY created_at ASC, id ASC
                           ) AS row_number
                    FROM user_memory_update_jobs
                    WHERE message_id IS NOT NULL
                )
                UPDATE user_memory_update_jobs
                SET message_id = NULL
                WHERE id IN (SELECT id FROM ranked WHERE row_number > 1)
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX uq_user_memory_update_jobs_user_message_id
                ON user_memory_update_jobs (user_id, message_id)
                WHERE message_id IS NOT NULL
                """
            )
        )


def make_column_nullable_if_supported(
    engine: Engine,
    inspector: Inspector,
    table_name: str,
    column_name: str,
) -> None:
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    column = columns.get(column_name)
    if not column or column.get("nullable"):
        return
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP NOT NULL"))

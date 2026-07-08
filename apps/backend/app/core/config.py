from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


PRODUCTION_ENVS = {"prod", "production"}
INSECURE_JWT_SECRETS = {"", "change-me", "dev-only-change-me", "dev-only-change-me-dev-secret-32-bytes"}
MIN_PRODUCTION_JWT_SECRET_LENGTH = 32
ALLOWED_MEMORY_UPDATE_MODES = {"sync", "async", "disabled"}


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "agentic-rag-platform"
    api_prefix: str = "/api"
    backend_cors_origins: str = "http://localhost:5173,http://localhost:3000"
    database_url: str = "sqlite+pysqlite:///./local_auth.db"
    auto_create_tables: bool = True
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "knowledge_chunks"
    memory_qdrant_collection: str = "user_memories"
    memory_vector_index_enabled: bool = False
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "documents"
    minio_secure: bool = False
    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 30
    agent_graph_backend: str = "langgraph"
    short_memory_max_messages: int = 12
    memory_context_max_chars: int = 3000
    memory_context_max_tokens: int = 900
    memory_update_mode: str = "sync"
    memory_semantic_threshold: float = 0.82
    embedding_provider: str = "openai_compatible"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 384
    embedding_batch_size: int = 10
    retrieval_top_k: int = 5
    retrieval_route_limit: int = 8
    rrf_k: int = 60
    context_compression_chunk_chars: int = 700
    answer_context_max_chars: int = 4000
    default_chunk_size: int = 800
    default_chunk_overlap: int = 120
    max_upload_size_mb: int = 50
    jwt_secret_key: str = "dev-only-change-me-dev-secret-32-bytes"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 14

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.backend_cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def cors_origin_regex(self) -> str | None:
        if self.app_env.lower() in PRODUCTION_ENVS:
            return None
        return r"^https?://(localhost|127\.0\.0\.1):\d+$"


def validate_runtime_settings(settings: Settings) -> None:
    if settings.app_env.lower() not in PRODUCTION_ENVS:
        return

    errors: list[str] = []
    if settings.jwt_secret_key.strip() in INSECURE_JWT_SECRETS:
        errors.append("JWT_SECRET_KEY must be set to a strong production secret")
    elif len(settings.jwt_secret_key.encode("utf-8")) < MIN_PRODUCTION_JWT_SECRET_LENGTH:
        errors.append(f"JWT_SECRET_KEY must be at least {MIN_PRODUCTION_JWT_SECRET_LENGTH} bytes in production")
    if settings.database_url.startswith("sqlite"):
        errors.append("DATABASE_URL must not use SQLite in production")
    if settings.auto_create_tables:
        errors.append("AUTO_CREATE_TABLES must be false in production")
    if "*" in settings.cors_origins:
        errors.append("BACKEND_CORS_ORIGINS must not allow '*' in production")
    if settings.llm_provider != "openai_compatible":
        errors.append("LLM_PROVIDER must be openai_compatible in production")
    if not settings.llm_api_key.strip():
        errors.append("LLM_API_KEY must be set in production")
    if settings.embedding_provider != "openai_compatible":
        errors.append("EMBEDDING_PROVIDER must be openai_compatible in production")
    if not settings.embedding_api_key.strip():
        errors.append("EMBEDDING_API_KEY must be set in production")
    if settings.memory_update_mode.strip().lower() not in ALLOWED_MEMORY_UPDATE_MODES:
        errors.append("MEMORY_UPDATE_MODE must be one of sync, async, disabled")

    if errors:
        raise RuntimeError("Invalid production settings: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()

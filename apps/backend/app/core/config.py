import re
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


PRODUCTION_ENVS = {"prod", "production"}
APP_ENV_ALIASES = {
    "dev": "development",
    "development": "development",
    "test": "test",
    "testing": "test",
    "prod": "production",
    "production": "production",
}
INSECURE_JWT_SECRETS = {"", "change-me", "dev-only-change-me", "dev-only-change-me-dev-secret-32-bytes"}
INSECURE_PRODUCTION_VALUES = {
    "",
    "change-me",
    "replace-me",
    "minioadmin",
    "rag_password",
    "password",
}
INSECURE_PRODUCTION_MARKERS = ("replace-with", "replace_me", "your-", "example")
MIN_PRODUCTION_JWT_SECRET_LENGTH = 32
ALLOWED_MEMORY_UPDATE_MODES = {"sync", "async", "disabled"}
ALLOWED_MODEL_PROVIDERS = {"openai_compatible"}
ALLOWED_JWT_ALGORITHMS = {"HS256"}


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "knowledge-work-assistant"
    api_prefix: str = "/api"
    backend_cors_origins: str = "http://localhost:5173,http://localhost:3000"
    database_url: str = "sqlite+pysqlite:///./local_auth.db"
    auto_create_tables: bool = True
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout_seconds: int = Field(default=30, ge=1)
    database_pool_recycle_seconds: int = Field(default=-1, ge=-1)
    database_pool_pre_ping: bool = True
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    redis_socket_timeout_seconds: float = Field(default=3.0, gt=0)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "knowledge_chunks"
    qdrant_timeout_seconds: int = Field(default=10, gt=0)
    healthcheck_timeout_seconds: int = Field(default=3, gt=0)
    memory_qdrant_collection: str = "user_memories"
    memory_vector_index_enabled: bool = True
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "documents"
    minio_secure: bool = False
    llm_provider: str = "openai_compatible"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = Field(default=30, gt=0)
    llm_default_temperature: float = Field(default=0.1, ge=0, le=2)
    llm_summary_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_memory_editor_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_context_compression_temperature: float = Field(default=0.0, ge=0, le=2)
    agent_stream_max_concurrency: int = Field(default=8, ge=1, le=256)
    agent_stream_queue_maxsize: int = Field(default=128, ge=1)
    agent_stream_min_timeout_seconds: float = Field(default=30.0, gt=0)
    agent_stream_timeout_llm_calls: int = Field(default=8, ge=1)
    agent_max_model_calls: int = Field(default=6, ge=2, le=20)
    agent_max_tool_calls: int = Field(default=4, ge=1, le=20)
    agent_max_memory_calls: int = Field(default=2, ge=0, le=10)
    agent_max_rag_calls: int = Field(default=3, ge=0, le=10)
    agent_tool_observation_max_chars: int = Field(default=2400, ge=200, le=20000)
    conversation_lease_grace_seconds: int = Field(default=30, ge=0)
    conversation_summary_dispatch_queue_size: int = Field(default=256, ge=1)
    short_memory_max_messages: int = Field(default=12, ge=1)
    short_memory_ttl_seconds: int = Field(default=86400, ge=1)
    short_memory_content_max_chars: int = Field(default=2000, ge=1)
    memory_context_max_chars: int = Field(default=3000, ge=1)
    memory_context_max_tokens: int = Field(default=1600, ge=1)
    memory_context_max_long_memories: int = Field(default=8, ge=1)
    memory_context_profile_weight: float = Field(default=0.25, ge=0)
    memory_context_long_term_weight: float = Field(default=0.35, ge=0)
    memory_context_summary_weight: float = Field(default=0.2, ge=0)
    memory_context_recent_weight: float = Field(default=0.2, ge=0)
    memory_context_empty_section_weight_factor: float = Field(default=0.2, ge=0, le=1)
    memory_context_min_section_tokens: int = Field(default=24, ge=1)
    memory_context_min_section_chars: int = Field(default=80, ge=1)
    memory_update_mode: str = "sync"
    memory_max_operations: int = Field(default=3, ge=1)
    memory_editor_context_limit: int = Field(default=30, ge=1)
    memory_editor_candidate_limit: int = Field(default=80, ge=1)
    memory_recall_candidate_limit: int = Field(default=120, ge=1)
    memory_source_max_chars: int = Field(default=700, ge=4)
    memory_summary_delta_max_chars: int = Field(default=12000, ge=1)
    memory_full_recall_limit: int = Field(default=20, ge=1)
    memory_profile_limit: int = Field(default=20, ge=1)
    memory_semantic_limit: int = Field(default=5, ge=1)
    memory_pending_limit: int = Field(default=10, ge=1)
    memory_reconcile_max_semantic_pairs: int = Field(default=2000, ge=1)
    conversation_summary_trigger_tokens: int = Field(default=2000, ge=1)
    conversation_summary_min_tokens: int = Field(default=500, ge=1)
    conversation_summary_min_messages: int = Field(default=16, ge=1)
    conversation_summary_max_unprocessed: int = Field(default=30, ge=1)
    conversation_summary_max_tokens: int = Field(default=1200, ge=1)
    conversation_summary_lease_min_seconds: int = Field(default=60, ge=1)
    llm_call_log_retention_days: int = Field(default=90, ge=0)
    retrieval_log_retention_days: int = Field(default=90, ge=0)
    agent_run_retention_days: int = Field(default=90, ge=0)
    memory_recall_log_retention_days: int = Field(default=90, ge=0)
    memory_update_job_retention_days: int = Field(default=30, ge=0)
    external_cleanup_job_retention_days: int = Field(default=30, ge=0)
    celery_task_max_retries: int = Field(default=3, ge=0)
    celery_task_retry_backoff_seconds: int = Field(default=5, ge=1)
    celery_task_retry_backoff_max_seconds: int = Field(default=300, ge=1)
    celery_result_expires_seconds: int = Field(default=86400, ge=1)
    celery_broker_visibility_timeout_min_seconds: int = Field(default=3600, ge=1)
    celery_broker_visibility_timeout_lease_multiplier: int = Field(default=3, ge=1)
    celery_worker_prefetch_multiplier: int = Field(default=1, ge=1)
    celery_operational_retention_task_expires_seconds: int = Field(default=82800, ge=1)
    worker_recovery_batch_size: int = Field(default=100, ge=1, le=500)
    worker_recovery_scan_multiplier: int = Field(default=5, ge=1, le=20)
    memory_update_job_lease_seconds: int = Field(default=600, ge=1)
    memory_update_job_recovery_interval_seconds: int = Field(default=60, ge=1)
    operational_retention_hour_utc: int = Field(default=2, ge=0, le=23)
    embedding_provider: str = "openai_compatible"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = Field(default=384, ge=1)
    embedding_batch_size: int = Field(default=10, ge=1)
    embedding_timeout_seconds: int = Field(default=30, gt=0)
    retrieval_top_k: int = Field(default=5, ge=1)
    retrieval_route_limit: int = Field(default=15, ge=1)
    retrieval_dense_prefilter_multiplier: int = Field(default=4, ge=1)
    retrieval_bm25_prefilter_terms: int = Field(default=12, ge=1)
    retrieval_max_matched_terms: int = Field(default=32, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    question_max_tokens: int = Field(default=1000, ge=1)
    rag_context_max_tokens: int = Field(default=6000, ge=1)
    context_compression_target_ratio: float = Field(default=0.9, gt=0, le=1)
    context_compression_retry_limit: int = Field(default=1, ge=0, le=3)
    default_chunk_size: int = Field(default=800, ge=1)
    default_chunk_overlap: int = Field(default=120, ge=0)
    max_upload_size_mb: int = Field(default=50, ge=1)
    transformers_verbosity: str = "error"
    bcrypt_rounds: int = Field(default=12, ge=4, le=31)
    jwt_secret_key: str = "dev-only-change-me-dev-secret-32-bytes"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=60, ge=1)
    jwt_refresh_token_expire_days: int = Field(default=14, ge=1)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_env(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized not in APP_ENV_ALIASES:
                raise ValueError(f"APP_ENV must be one of {', '.join(sorted(APP_ENV_ALIASES))}")
            return APP_ENV_ALIASES[normalized]
        return value

    @field_validator("memory_update_mode", mode="before")
    @classmethod
    def validate_memory_update_mode(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized not in ALLOWED_MEMORY_UPDATE_MODES:
                raise ValueError("MEMORY_UPDATE_MODE must be one of sync, async, disabled")
            return normalized
        return value

    @field_validator("llm_provider", "embedding_provider", mode="before")
    @classmethod
    def validate_model_provider(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized not in ALLOWED_MODEL_PROVIDERS:
                raise ValueError("Model provider must be openai_compatible")
            return normalized
        return value

    @field_validator("jwt_algorithm", mode="before")
    @classmethod
    def validate_jwt_algorithm(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized not in ALLOWED_JWT_ALGORITHMS:
                raise ValueError("JWT_ALGORITHM must be HS256")
            return normalized
        return value

    @field_validator("api_prefix", mode="before")
    @classmethod
    def validate_api_prefix(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            path_segments = normalized.split("/")[1:]
            if (
                normalized == "/"
                or re.fullmatch(r"/[A-Za-z0-9._~/-]+", normalized) is None
                or normalized.endswith("/")
                or "//" in normalized
                or any(segment in {".", ".."} for segment in path_segments)
            ):
                raise ValueError("API_PREFIX must be a non-root path such as /api, without a trailing slash")
            return normalized
        return value

    @model_validator(mode="after")
    def validate_hyperparameters(self) -> "Settings":
        errors: list[str] = []
        if self.default_chunk_overlap >= self.default_chunk_size:
            errors.append("DEFAULT_CHUNK_OVERLAP must be smaller than DEFAULT_CHUNK_SIZE")
        if self.conversation_summary_min_tokens > self.conversation_summary_trigger_tokens:
            errors.append("CONVERSATION_SUMMARY_MIN_TOKENS must not exceed CONVERSATION_SUMMARY_TRIGGER_TOKENS")
        if self.conversation_summary_min_messages > self.conversation_summary_max_unprocessed:
            errors.append("CONVERSATION_SUMMARY_MIN_MESSAGES must not exceed CONVERSATION_SUMMARY_MAX_UNPROCESSED")
        if not any(
            weight > 0
            for weight in (
                self.memory_context_profile_weight,
                self.memory_context_long_term_weight,
                self.memory_context_summary_weight,
                self.memory_context_recent_weight,
            )
        ):
            errors.append("At least one MEMORY_CONTEXT_*_WEIGHT must be greater than zero")
        if self.celery_task_retry_backoff_max_seconds < self.celery_task_retry_backoff_seconds:
            errors.append("CELERY_TASK_RETRY_BACKOFF_MAX_SECONDS must cover the initial retry backoff")
        for origin in self.cors_origins:
            if origin == "*":
                continue
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                errors.append(f"BACKEND_CORS_ORIGINS contains an invalid origin: {origin}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.backend_cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def cors_origin_regex(self) -> str | None:
        if self.app_env in PRODUCTION_ENVS:
            return None
        return r"^https?://(localhost|127\.0\.0\.1):\d+$"


def validate_runtime_settings(settings: Settings) -> None:
    if settings.app_env not in PRODUCTION_ENVS:
        return

    errors: list[str] = []
    if settings.jwt_secret_key.strip() in INSECURE_JWT_SECRETS or is_placeholder_secret(settings.jwt_secret_key):
        errors.append("JWT_SECRET_KEY must be set to a strong production secret")
    elif len(settings.jwt_secret_key.encode("utf-8")) < MIN_PRODUCTION_JWT_SECRET_LENGTH:
        errors.append(f"JWT_SECRET_KEY must be at least {MIN_PRODUCTION_JWT_SECRET_LENGTH} bytes in production")
    if settings.database_url.startswith("sqlite"):
        errors.append("DATABASE_URL must not use SQLite in production")
    elif has_insecure_database_credentials(settings.database_url):
        errors.append("DATABASE_URL must not contain placeholder or default credentials in production")
    if settings.auto_create_tables:
        errors.append("AUTO_CREATE_TABLES must be false in production")
    if "*" in settings.cors_origins:
        errors.append("BACKEND_CORS_ORIGINS must not allow '*' in production")
    production_origins = [origin for origin in settings.cors_origins if origin != "*"]
    if not production_origins or any(urlsplit(origin).scheme != "https" for origin in production_origins):
        errors.append("BACKEND_CORS_ORIGINS must contain only HTTPS origins in production")
    if any(is_placeholder_origin(origin) for origin in settings.cors_origins):
        errors.append("BACKEND_CORS_ORIGINS must not contain placeholder origins in production")
    if settings.llm_provider != "openai_compatible":
        errors.append("LLM_PROVIDER must be openai_compatible in production")
    if not settings.llm_api_key.strip() or is_placeholder_secret(settings.llm_api_key):
        errors.append("LLM_API_KEY must be set to a real production credential")
    if settings.embedding_provider != "openai_compatible":
        errors.append("EMBEDDING_PROVIDER must be openai_compatible in production")
    if not settings.embedding_api_key.strip() or is_placeholder_secret(settings.embedding_api_key):
        errors.append("EMBEDDING_API_KEY must be set to a real production credential")
    if is_placeholder_secret(settings.minio_access_key):
        errors.append("MINIO_ACCESS_KEY must be set to a non-default production credential")
    if is_placeholder_secret(settings.minio_secret_key):
        errors.append("MINIO_SECRET_KEY must be set to a non-default production credential")
    if settings.memory_update_mode.strip().lower() not in ALLOWED_MEMORY_UPDATE_MODES:
        errors.append("MEMORY_UPDATE_MODE must be one of sync, async, disabled")
    if settings.celery_task_max_retries < 0:
        errors.append("CELERY_TASK_MAX_RETRIES must be zero or greater")
    if settings.celery_task_retry_backoff_seconds <= 0:
        errors.append("CELERY_TASK_RETRY_BACKOFF_SECONDS must be greater than zero")
    memory_job_llm_call_budget = 1 + settings.memory_max_operations
    minimum_memory_job_lease = settings.llm_timeout_seconds * memory_job_llm_call_budget
    if settings.memory_update_job_lease_seconds < minimum_memory_job_lease:
        errors.append(
            "MEMORY_UPDATE_JOB_LEASE_SECONDS must cover the memory review and operation LLM call budget "
            f"(at least {minimum_memory_job_lease} seconds for the configured LLM timeout)"
        )
    if settings.memory_update_job_recovery_interval_seconds <= 0:
        errors.append("MEMORY_UPDATE_JOB_RECOVERY_INTERVAL_SECONDS must be greater than zero")
    if not 0 <= settings.operational_retention_hour_utc <= 23:
        errors.append("OPERATIONAL_RETENTION_HOUR_UTC must be between 0 and 23")

    if errors:
        raise RuntimeError("Invalid production settings: " + "; ".join(errors))


def is_placeholder_secret(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in INSECURE_PRODUCTION_VALUES:
        return True
    return any(marker in normalized for marker in INSECURE_PRODUCTION_MARKERS)


def has_insecure_database_credentials(value: str) -> bool:
    try:
        url = make_url(value)
    except ArgumentError:
        return True
    return not url.username or not url.password or is_placeholder_secret(url.password)


def is_placeholder_origin(value: str) -> bool:
    hostname = (urlsplit(value).hostname or "").lower()
    return (
        hostname.startswith(("your-", "replace-"))
        or hostname in {"localhost", "127.0.0.1", "::1", "example.com", "example.org", "example.net"}
        or hostname.endswith(
            (".localhost", ".example.com", ".example.org", ".example.net", ".test", ".invalid")
        )
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

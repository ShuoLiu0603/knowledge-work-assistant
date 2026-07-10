from datetime import datetime

from pydantic import BaseModel, Field

from app.core.security_levels import MAX_SECURITY_LEVEL, MIN_SECURITY_LEVEL


class AdminMetricsRead(BaseModel):
    generated_at: datetime
    scope: str
    conversation_count: int
    message_count: int
    retrieval_log_count: int
    llm_call_count: int
    total_tokens: int
    average_llm_latency_ms: float | None
    fallback_call_count: int
    feedback_count: int
    positive_feedback_count: int
    negative_feedback_count: int
    positive_feedback_rate: float | None
    average_selected_chunks: float | None
    external_cleanup_job_count: int
    failed_external_cleanup_job_count: int
    queued_external_cleanup_job_count: int
    recent_llm_errors: list[dict]


class AdminUserRead(BaseModel):
    id: str
    email: str
    username: str
    is_active: bool
    is_admin: bool
    security_level: int
    department_id: str | None
    department_name: str | None
    created_at: datetime


class AdminUserUpdate(BaseModel):
    security_level: int | None = Field(default=None, ge=MIN_SECURITY_LEVEL, le=MAX_SECURITY_LEVEL)
    is_active: bool | None = None
    is_admin: bool | None = None
    department_id: str | None = None


class AuditLogRead(BaseModel):
    id: str
    actor_user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    security_level: int | None
    detail: str | None
    metadata: dict
    created_at: datetime


class ExternalCleanupJobRead(BaseModel):
    id: str
    actor_user_id: str | None
    resource_type: str
    resource_id: str
    action: str
    status: str
    attempts: int
    object_keys: list
    error_message: str
    metadata: dict
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class RetentionRunRead(BaseModel):
    generated_at: datetime
    dry_run: bool
    deleted_counts: dict[str, int]
    cutoffs: dict[str, datetime | None]

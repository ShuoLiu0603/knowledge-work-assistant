from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserMemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=2000)
    confirm_sensitive: bool = False


class UserMemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    status: str | None = Field(default=None, max_length=30)
    confirm_sensitive: bool = False


class UserMemoryRead(BaseModel):
    id: str
    user_id: str
    content: str
    content_hash: str
    status: str
    kind: str
    category: str
    canonical_key: str
    memory_layer: str
    profile_slot: str
    scope_type: str
    scope_id: str
    pinned: bool
    revision: int
    expires_at: datetime | None
    source_text: str
    source_conversation_id: str | None
    source_message_id: str | None
    embedding_model: str
    embedding_dimension: int
    merge_count: int
    touched_count: int
    superseded_by_id: str | None
    metadata: dict
    valid_at: datetime
    invalid_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_touched_at: datetime


class UserMemoryUpdateJobRead(BaseModel):
    id: str
    user_id: str
    conversation_id: str | None
    message_id: str | None
    user_message: str
    assistant_message: str
    status: str
    attempts: int
    actions: list
    error_message: str
    lease_expires_at: datetime | None
    dispatched_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserMemoryEventRead(BaseModel):
    id: str
    user_id: str
    memory_id: str | None
    event_type: str
    actor_type: str
    actor_user_id: str | None
    source: str
    reason: str
    previous_status: str | None
    new_status: str | None
    payload: dict
    created_at: datetime


class UserMemoryRecallLogRead(BaseModel):
    id: str
    user_id: str
    conversation_id: str | None
    message_id: str | None
    query: str
    recall_mode: str
    requested_limit: int
    recall_limit: int
    active_count: int
    selected_count: int
    threshold: float | None
    candidates: list
    selected_memory_ids: list
    created_at: datetime


class UserMemoryExportRead(BaseModel):
    user_id: str
    exported_at: datetime
    memories: list[UserMemoryRead]
    events: list[UserMemoryEventRead]
    recall_logs: list[UserMemoryRecallLogRead]
    update_jobs: list[UserMemoryUpdateJobRead]


class UserMemoryRecallMetricsRead(BaseModel):
    user_id: str
    total_logs: int
    recall_mode_counts: dict[str, int]
    route_counts: dict[str, int]
    route_selected_counts: dict[str, int]
    category_counts: dict[str, int]
    memory_layer_counts: dict[str, int]
    profile_slot_counts: dict[str, int]
    empty_result_count: int
    empty_result_rate: float
    fallback_count: int
    vector_count: int
    below_threshold_candidate_count: int
    average_selected_count: float
    average_active_count: float
    average_top_score: float | None
    unique_selected_memory_count: int
    top_selected_memories: list[dict]


class UserMemoryReconcileFindingRead(BaseModel):
    finding_type: str
    severity: str
    memory_id: str
    related_memory_id: str | None
    proposed_action: str
    reason: str
    applied: bool
    metadata: dict


class UserMemoryReconcileRead(BaseModel):
    user_id: str
    apply: bool
    scanned_count: int
    applied_count: int
    findings: list[UserMemoryReconcileFindingRead]

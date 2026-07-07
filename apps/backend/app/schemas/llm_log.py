from datetime import datetime

from pydantic import BaseModel


class LlmCallLogRead(BaseModel):
    id: str
    user_id: str | None
    conversation_id: str | None
    agent_name: str | None
    provider: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float | None
    latency_ms: int | None
    status: str
    fallback_used: bool
    error_message: str | None
    created_at: datetime

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.schemas.qa import CitationRead


@dataclass
class AgentGraphState:
    user_id: str
    knowledge_base_id: str | None
    input: str
    top_k: int | None = None
    search_scope: str = "single"
    search_department_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    intent: str = "rag"
    answer: str = ""
    citations: list[CitationRead] = field(default_factory=list)
    retrieval_log_id: str | None = None
    llm_log_id: str | None = None
    llm_log_ids: list[str] = field(default_factory=list)
    short_term_memory: list[dict] = field(default_factory=list)
    long_term_memories: list[dict] = field(default_factory=list)
    conversation_summary: str | None = None
    memory_context: str = ""
    memory_actions: list[dict] = field(default_factory=list)
    token_callback: Callable[[str], None] | None = None
    trace: list[dict] = field(default_factory=list)
    status: str = "running"
    error_message: str | None = None


def add_trace(
    state: AgentGraphState,
    node: str,
    action: str,
    input_data: dict,
    output_data: dict,
) -> None:
    state.trace.append(
        {
            "node": node,
            "action": action,
            "input": input_data,
            "output": output_data,
        }
    )

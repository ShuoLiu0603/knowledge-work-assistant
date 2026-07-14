from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event
from time import monotonic

from app.schemas.qa import CitationRead
from app.rag.retrieval import RetrievedChunk


class AgentRunCancelled(RuntimeError):
    pass


class AgentRunTimeout(RuntimeError):
    pass


@dataclass
class AgentRunState:
    user_id: str
    knowledge_base_id: str | None
    input: str
    top_k: int | None = None
    search_scope: str = "single"
    search_department_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    answer: str = ""
    citations: list[CitationRead] = field(default_factory=list)
    retrieval_log_id: str | None = None
    llm_log_id: str | None = None
    llm_log_ids: list[str] = field(default_factory=list)
    short_term_memory: list[dict] = field(default_factory=list)
    profile_memories: list[dict] = field(default_factory=list)
    long_term_memories: list[dict] = field(default_factory=list)
    conversation_summary: str | None = None
    core_memory_context: str = ""
    memory_context: str = ""
    memory_actions: list[dict] = field(default_factory=list)
    defer_memory_update: bool = False
    memory_enabled: bool | None = None
    memory_recalled: bool = False
    rag_chunks: list[RetrievedChunk] = field(default_factory=list)
    rag_batches: list[list[str]] = field(default_factory=list)
    rag_searched: bool = False
    retrieval_log_ids: list[str] = field(default_factory=list)
    memory_queries: list[str] = field(default_factory=list)
    rag_queries: list[str] = field(default_factory=list)
    tool_observations: list[dict] = field(default_factory=list)
    executed_tool_calls: list[str] = field(default_factory=list)
    tool_call_count: int = 0
    memory_tool_call_count: int = 0
    rag_tool_call_count: int = 0
    model_call_count: int = 0
    token_callback: Callable[[str], None] | None = None
    cancel_event: Event | None = field(default=None, repr=False)
    deadline_monotonic: float | None = field(default=None, repr=False)
    searched_knowledge_base_ids: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    status: str = "running"
    error_message: str | None = None


def ensure_agent_run_active(state: AgentRunState) -> None:
    if state.cancel_event is not None and state.cancel_event.is_set():
        raise AgentRunCancelled("Agent run cancelled")
    if state.deadline_monotonic is not None and monotonic() >= state.deadline_monotonic:
        if state.cancel_event is not None:
            state.cancel_event.set()
        raise AgentRunTimeout("Agent run timed out")


def add_trace(
    state: AgentRunState,
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

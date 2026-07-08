from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from datetime import datetime, timezone
from typing import Any, TypedDict

from sqlalchemy.orm import Session

from app.agents.memory_agent import load_memory_context, update_user_memories
from app.agents.rag_agent import answer_with_rag
from app.agents.state import AgentGraphState, add_trace
from app.agents.summary_agent import summarize_with_rag
from app.agents.supervisor import route_intent
from app.agents.tools import ensure_viewer_access
from app.agents.writing_agent import draft_with_rag
from app.core.config import get_settings


class AgentGraphPayload(TypedDict):
    state: AgentGraphState


AgentNode = Callable[[Session, AgentGraphState], AgentGraphState]


def run_agent_graph(db: Session, state: AgentGraphState) -> AgentGraphState:
    started_at = datetime.now(timezone.utc)
    if state.knowledge_base_id:
        ensure_viewer_access(db, state.user_id, state.knowledge_base_id)
    requested_backend = normalize_backend(get_settings().agent_graph_backend)
    actual_backend = requested_backend
    try:
        if requested_backend == "langgraph":
            returned_state = _run_langgraph_nodes(db, state)
            if returned_state is not state:
                copy_state_values(returned_state, state)
        else:
            actual_backend = "sequential"
            _run_sequential_nodes(db, state)
        if state.status == "running":
            state.status = "completed"
    except Exception as exc:
        state.status = "failed"
        state.error_message = str(exc)
        add_trace(
            state,
            node="error",
            action="capture_exception",
            input_data={"intent": state.intent},
            output_data={"error_message": state.error_message},
        )
    finally:
        add_trace(
            state,
            node="graph",
            action="complete_run",
            input_data={"started_at": started_at.isoformat()},
            output_data={
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "requested_backend": requested_backend,
                "backend": actual_backend,
                "status": state.status,
            },
        )
    return state


def copy_state_values(source: AgentGraphState, target: AgentGraphState) -> None:
    for field in fields(AgentGraphState):
        setattr(target, field.name, getattr(source, field.name))


def normalize_backend(value: str | None) -> str:
    normalized = (value or "langgraph").strip().lower()
    return normalized if normalized in {"langgraph", "sequential"} else "langgraph"


def _run_sequential_nodes(db: Session, state: AgentGraphState) -> AgentGraphState:
    load_memory_context(db, state)
    route_intent(db, state)
    _run_intent_node(db, state)
    update_user_memories(db, state)
    return state


def _run_langgraph_nodes(db: Session, state: AgentGraphState) -> AgentGraphState:
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise ImportError("LangGraph is required when AGENT_GRAPH_BACKEND=langgraph.") from exc

    graph = build_agent_graph(db, StateGraph, END)
    result = graph.compile().invoke({"state": state})
    return result["state"]


def build_agent_graph(db: Session, state_graph_cls, end_node):
    graph = state_graph_cls(AgentGraphPayload)
    graph.add_node("load_memory", langgraph_node(db, load_memory_context))
    graph.add_node("supervisor", langgraph_node(db, route_intent))
    graph.add_node("rag_agent", langgraph_node(db, answer_with_rag))
    graph.add_node("summary_agent", langgraph_node(db, summarize_with_rag))
    graph.add_node("writing_agent", langgraph_node(db, draft_with_rag))
    graph.add_node("update_memory", langgraph_node(db, update_user_memories))

    graph.set_entry_point("load_memory")
    graph.add_edge("load_memory", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        {
            "rag_agent": "rag_agent",
            "summary_agent": "summary_agent",
            "writing_agent": "writing_agent",
        },
    )
    graph.add_edge("rag_agent", "update_memory")
    graph.add_edge("summary_agent", "update_memory")
    graph.add_edge("writing_agent", "update_memory")
    graph.add_edge("update_memory", end_node)
    return graph


def langgraph_node(db: Session, handler: AgentNode) -> Callable[[AgentGraphPayload], AgentGraphPayload]:
    def run(payload: AgentGraphPayload) -> AgentGraphPayload:
        return {"state": handler(db, payload["state"])}

    return run


def _route_after_supervisor(payload: dict[str, Any]) -> str:
    intent = payload["state"].intent
    if intent == "summary":
        return "summary_agent"
    if intent == "writing":
        return "writing_agent"
    return "rag_agent"


def _run_intent_node(db: Session, state: AgentGraphState) -> AgentGraphState:
    if state.intent == "summary":
        return summarize_with_rag(db, state)
    if state.intent == "writing":
        return draft_with_rag(db, state)
    return answer_with_rag(db, state)

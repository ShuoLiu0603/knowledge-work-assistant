from __future__ import annotations

import json
import time
from queue import Empty, Queue
from threading import Event, Thread
from collections.abc import Iterator
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.agent_run import AgentRun
from app.db.models.conversation import Conversation, Message
from app.db.models.llm_call_log import LlmCallLog
from app.db.models.retrieval_log import RetrievalLog
from app.schemas.conversation import ConversationCreate, ConversationRead, MessageRead, StreamMessageRequest
from app.schemas.qa import CitationRead
from app.services.agent_service import attach_agent_run_to_message, run_agent, to_agent_run_read
from app.services.audit_service import record_audit_event
from app.services.knowledge_base_service import ensure_kb_access, resolve_search_scope
from app.services.memory_service import append_short_term_memory, should_update_conversation_summary, update_conversation_summary
from app.services.retrieval_log_service import attach_retrieval_log_to_message, to_retrieval_log_read


class AgentRunCancelled(RuntimeError):
    pass


def create_conversation(db: Session, user_id: str, payload: ConversationCreate) -> ConversationRead:
    scope = resolve_search_scope(
        db,
        user_id,
        payload.knowledge_base_id,
        scope_type=payload.search_scope,
        department_id=payload.department_id,
    )
    title = normalize_title(payload.title) or "新会话"
    conversation = Conversation(
        user_id=user_id,
        knowledge_base_id=scope.primary_knowledge_base_id,
        search_scope=scope.scope_type,
        search_department_id=scope.department_id,
        title=title,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return to_conversation_read(conversation)


def list_conversations(
    db: Session,
    user_id: str,
    knowledge_base_id: str | None = None,
    search_scope: str | None = None,
) -> list[ConversationRead]:
    if knowledge_base_id:
        ensure_kb_access(db, user_id, knowledge_base_id, required_role="viewer")

    query = select(Conversation).where(Conversation.user_id == user_id)
    if knowledge_base_id:
        query = query.where(Conversation.knowledge_base_id == knowledge_base_id)
    if search_scope:
        query = query.where(Conversation.search_scope == search_scope)

    conversations = db.scalars(query.order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())).all()
    return [to_conversation_read(conversation) for conversation in conversations]


def get_conversation(db: Session, user_id: str, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if conversation.knowledge_base_id:
        ensure_kb_access(db, user_id, conversation.knowledge_base_id, required_role="viewer")
    return conversation


def get_conversation_detail(db: Session, user_id: str, conversation_id: str) -> ConversationRead:
    return to_conversation_read(get_conversation(db, user_id, conversation_id))


def delete_conversation(db: Session, user_id: str, conversation_id: str) -> None:
    conversation = get_conversation(db, user_id, conversation_id)
    title = conversation.title
    knowledge_base_id = conversation.knowledge_base_id
    search_scope = conversation.search_scope
    db.delete(conversation)
    db.commit()
    record_audit_event(
        db,
        actor_user_id=user_id,
        action="conversation.delete",
        resource_type="conversation",
        resource_id=conversation_id,
        metadata={
            "knowledge_base_id": knowledge_base_id,
            "search_scope": search_scope,
            "title": title,
        },
    )


def list_messages(db: Session, user_id: str, conversation_id: str) -> list[MessageRead]:
    conversation = get_conversation(db, user_id, conversation_id)
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).all()
    return [to_message_read(message) for message in messages]


def stream_message_response(
    db: Session,
    user_id: str,
    conversation_id: str,
    payload: StreamMessageRequest,
) -> Iterator[str]:
    cancel_event = Event()
    question = payload.question.strip()
    if not question:
        yield sse_event("error", {"message": "Question cannot be empty"})
        return

    try:
        conversation = get_conversation(db, user_id, conversation_id)
        if conversation.title == "新会话":
            conversation.title = title_from_question(question)

        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=question,
            status="completed",
        )
        conversation.updated_at = datetime.now(timezone.utc)
        db.add_all([conversation, user_message])
        db.commit()
        db.refresh(conversation)
        db.refresh(user_message)

        yield sse_event("conversation", to_conversation_read(conversation).model_dump(mode="json"))
        yield sse_event("user_message", to_message_read(user_message).model_dump(mode="json"))
        append_short_term_memory(user_id, conversation.id, "user", question)

        yield sse_event("trace", {"node": "agent_graph", "status": "started"})
        agent_run = None
        token_emitted = False
        for stream_event in run_agent_streaming(
            db,
            user_id,
            conversation.knowledge_base_id,
            question,
            top_k=payload.top_k,
            search_scope=conversation.search_scope,
            department_id=conversation.search_department_id,
            conversation_id=conversation.id,
            message_id=user_message.id,
            cancel_event=cancel_event,
        ):
            if stream_event["type"] == "token":
                token_emitted = True
                yield sse_event("token", {"content": stream_event["content"]})
            elif stream_event["type"] == "done":
                agent_run = stream_event["agent_run"]
                token_emitted = bool(stream_event["token_emitted"])
        if agent_run is None:
            raise RuntimeError("Agent run did not complete")
        yield sse_event("trace", {"node": "agent_graph", "status": agent_run.status})
        citations = [CitationRead(**citation) for citation in agent_run.citations]
        answer = agent_run.answer
        yield sse_event("agent_run", to_agent_run_read(agent_run).model_dump(mode="json"))
        retrieval_log = get_run_retrieval_log(db, agent_run)
        if retrieval_log:
            yield sse_event("retrieval_log", to_retrieval_log_read(retrieval_log).model_dump(mode="json"))
        yield sse_event("citations", {"citations": [citation.model_dump(mode="json") for citation in citations]})

        if not token_emitted:
            for token in tokenize_answer(answer):
                yield sse_event("token", {"content": token})
                time.sleep(0.01)

        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            status=agent_run.status,
            citations=[citation.model_dump(mode="json") for citation in citations],
            agent_trace=agent_run.trace,
            token_usage=message_token_usage(db, agent_run),
            error_message=agent_run.error_message,
        )
        conversation.updated_at = datetime.now(timezone.utc)
        db.add_all([conversation, assistant_message])
        db.commit()
        db.refresh(assistant_message)
        agent_run = attach_agent_run_to_message(db, agent_run, assistant_message.id)
        if retrieval_log:
            retrieval_log = attach_retrieval_log_to_message(db, retrieval_log, assistant_message.id)
        append_short_term_memory(user_id, conversation.id, "assistant", answer)
        maybe_update_conversation_summary(db, user_id, conversation, question, answer)

        yield sse_event("assistant_message", to_message_read(assistant_message).model_dump(mode="json"))
        yield sse_event("agent_run", to_agent_run_read(agent_run).model_dump(mode="json"))
        if retrieval_log:
            yield sse_event("retrieval_log", to_retrieval_log_read(retrieval_log).model_dump(mode="json"))
        yield sse_event("done", {"conversation_id": conversation.id, "message_id": assistant_message.id})
    except AgentRunCancelled:
        db.rollback()
        return
    except Exception as exc:
        db.rollback()
        error_message = str(exc)
        try:
            conversation = get_conversation(db, user_id, conversation_id)
            failed_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="",
                status="failed",
                error_message=error_message,
            )
            conversation.updated_at = datetime.now(timezone.utc)
            db.add_all([conversation, failed_message])
            db.commit()
            db.refresh(failed_message)
            yield sse_event("assistant_message", to_message_read(failed_message).model_dump(mode="json"))
        except Exception:
            db.rollback()
        yield sse_event("error", {"message": error_message})
    finally:
        cancel_event.set()


def run_agent_streaming(
    db: Session,
    user_id: str,
    knowledge_base_id: str | None,
    question: str,
    top_k: int | None,
    search_scope: str,
    department_id: str | None,
    conversation_id: str,
    message_id: str | None = None,
    cancel_event: Event | None = None,
) -> Iterator[dict]:
    queue: Queue[tuple[str, object | None]] = Queue()
    WorkerSession = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)

    def enqueue_token(token: str) -> None:
        if cancel_event and cancel_event.is_set():
            raise AgentRunCancelled("Agent run cancelled")
        if token:
            queue.put(("token", token))

    def worker() -> None:
        worker_db = WorkerSession()
        try:
            run = run_agent(
                worker_db,
                user_id,
                knowledge_base_id,
                question,
                top_k=top_k,
                search_scope=search_scope,
                department_id=department_id,
                conversation_id=conversation_id,
                message_id=message_id,
                on_token=enqueue_token,
            )
            queue.put(("agent_run_id", run.id))
        except AgentRunCancelled as exc:
            worker_db.rollback()
            queue.put(("cancelled", str(exc)))
        except Exception as exc:
            worker_db.rollback()
            queue.put(("error", str(exc)))
        finally:
            worker_db.close()
            queue.put(("done", None))

    thread = Thread(target=worker, daemon=True)
    thread.start()

    agent_run_id: str | None = None
    error_message = ""
    token_emitted = False
    while True:
        if cancel_event and cancel_event.is_set():
            raise AgentRunCancelled("Agent run cancelled")
        try:
            event, value = queue.get(timeout=0.25)
        except Empty:
            continue
        if event == "token" and value:
            token_emitted = True
            yield {"type": "token", "content": value}
        elif event == "agent_run_id":
            agent_run_id = str(value)
        elif event == "cancelled":
            raise AgentRunCancelled(str(value) if value else "Agent run cancelled")
        elif event == "error" and value:
            error_message = str(value)
        elif event == "done":
            break

    thread.join()
    if error_message:
        raise RuntimeError(error_message)
    if not agent_run_id:
        raise RuntimeError("Agent run did not return an id")
    agent_run = db.get(AgentRun, agent_run_id)
    if agent_run is None:
        raise RuntimeError("Agent run did not complete")
    yield {"type": "done", "agent_run": agent_run, "token_emitted": token_emitted}


def to_conversation_read(conversation: Conversation) -> ConversationRead:
    return ConversationRead(
        id=conversation.id,
        knowledge_base_id=conversation.knowledge_base_id,
        knowledge_base_name=conversation.knowledge_base.name if conversation.knowledge_base else None,
        search_scope=conversation.search_scope,
        search_department_id=conversation.search_department_id,
        target_label=conversation_target_label(conversation),
        title=conversation.title,
        summary=conversation.summary,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def to_message_read(message: Message) -> MessageRead:
    return MessageRead(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        status=message.status,
        citations=[CitationRead(**citation) for citation in message.citations],
        agent_trace=message.agent_trace or [],
        token_usage=message.token_usage or {},
        error_message=message.error_message,
        created_at=message.created_at,
    )


def sse_event(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def tokenize_answer(answer: str) -> Iterator[str]:
    current = ""
    for char in answer:
        current += char
        if char.isspace() or char in "，。；：,.!?！？\n":
            yield current
            current = ""
    if current:
        yield current


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    return title.strip()[:160]


def title_from_question(question: str) -> str:
    normalized = " ".join(question.split())
    if len(normalized) <= 42:
        return normalized
    return normalized[:41].rstrip() + "…"


def conversation_target_label(conversation: Conversation) -> str:
    if conversation.search_scope == "single":
        return conversation.knowledge_base.name if conversation.knowledge_base else "单个知识库"
    if conversation.search_scope == "department":
        return f"{conversation.search_department.name} 部门知识库" if conversation.search_department else "部门知识库"
    if conversation.search_scope == "public":
        return "公共知识库"
    return "所有知识库"


def maybe_update_conversation_summary(
    db: Session,
    user_id: str,
    conversation: Conversation,
    question: str,
    answer: str,
) -> None:
    if not should_update_conversation_summary(db, conversation.id):
        return
    try:
        update_conversation_summary(db, conversation, question, answer, user_id=user_id)
    except Exception:
        db.rollback()


def get_run_retrieval_log(db: Session, run: AgentRun) -> RetrievalLog | None:
    if not run.retrieval_log_id:
        return None
    return db.get(RetrievalLog, run.retrieval_log_id)


def message_token_usage(db: Session, run: AgentRun) -> dict:
    llm_log_id = (run.state or {}).get("llm_log_id")
    llm_log_ids = list(dict.fromkeys((run.state or {}).get("llm_log_ids") or []))
    if llm_log_id and llm_log_id not in llm_log_ids:
        llm_log_ids.append(llm_log_id)
    if not llm_log_ids:
        return {}
    logs = [log for log_id in llm_log_ids if (log := db.get(LlmCallLog, log_id)) is not None]
    if not logs:
        return {"llm_log_id": llm_log_id, "llm_log_ids": llm_log_ids}
    primary = logs[-1]
    return {
        "llm_log_id": primary.id,
        "llm_log_ids": [log.id for log in logs],
        "provider": primary.provider,
        "model_name": primary.model_name,
        "prompt_tokens": sum(log.prompt_tokens for log in logs),
        "completion_tokens": sum(log.completion_tokens for log in logs),
        "total_tokens": sum(log.total_tokens for log in logs),
        "latency_ms": sum(log.latency_ms or 0 for log in logs),
        "status": "failed" if any(log.status == "failed" for log in logs) else primary.status,
        "fallback_used": any(log.fallback_used for log in logs),
    }

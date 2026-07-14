from __future__ import annotations

import json
import time
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from collections.abc import Iterator
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.state import AgentRunCancelled, AgentRunTimeout
from app.core.config import PRODUCTION_ENVS, get_settings
from app.db.models.agent_run import AgentRun
from app.db.models.conversation import Conversation, Message
from app.db.models.llm_call_log import LlmCallLog
from app.db.models.retrieval_log import RetrievalLog
from app.memory import short_term
from app.schemas.conversation import ConversationCreate, ConversationRead, MessageRead, StreamMessageRequest
from app.schemas.qa import CitationRead
from app.services.agent_service import (
    apply_deferred_memory_update,
    attach_agent_run_to_message,
    ensure_agent_run_access,
    run_agent,
    to_agent_run_read,
)
from app.services.audit_service import record_audit_event
from app.services.knowledge_base_service import ensure_kb_access, list_knowledge_bases, resolve_search_scope
from app.services.memory_service import (
    append_short_term_memory,
    should_skip_memory_for_turn,
)
from app.services.retrieval_log_service import (
    attach_retrieval_log_to_message,
    ensure_retrieval_log_access,
    to_retrieval_log_read,
)


_SETTINGS = get_settings()
AGENT_STREAM_QUEUE_MAXSIZE = _SETTINGS.agent_stream_queue_maxsize
AGENT_STREAM_MIN_TIMEOUT_SECONDS = _SETTINGS.agent_stream_min_timeout_seconds
AGENT_STREAM_TIMEOUT_LLM_CALLS = _SETTINGS.agent_stream_timeout_llm_calls
CONVERSATION_LEASE_GRACE_SECONDS = _SETTINGS.conversation_lease_grace_seconds
CONVERSATION_LEASE_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
_PROCESS_CONVERSATION_LOCKS: dict[str, Lock] = {}
_PROCESS_CONVERSATION_LOCKS_GUARD = Lock()
_AGENT_STREAM_CAPACITY_GUARD = Lock()
_AGENT_STREAM_ACTIVE = 0
_SUMMARY_DISPATCH_QUEUE: Queue[tuple[str, str]] = Queue(
    maxsize=_SETTINGS.conversation_summary_dispatch_queue_size
)
_SUMMARY_DISPATCH_THREAD: Thread | None = None
_SUMMARY_DISPATCH_THREAD_GUARD = Lock()


@dataclass(frozen=True)
class ConversationRunLease:
    key: str
    token: str
    redis_client: object | None = None
    process_lock: Lock | None = None


class AgentStreamSlot:
    def __init__(self) -> None:
        self._guard = Lock()
        self._worker_owned = False
        self._released = False

    def transfer_to_worker(self) -> bool:
        with self._guard:
            if self._released:
                return False
            self._worker_owned = True
            return True

    def release_request(self) -> None:
        with self._guard:
            if self._released or self._worker_owned:
                return
            self._released = True
        release_agent_stream_capacity()

    def release_worker(self) -> None:
        with self._guard:
            if self._released:
                return
            self._released = True
        release_agent_stream_capacity()


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
    accessible_knowledge_base_ids = {item.id for item in list_knowledge_bases(db, user_id)}
    visible = []
    for conversation in conversations:
        if not conversation.history_provenance_complete:
            continue
        provenance_ids = conversation_provenance_ids(conversation)
        if provenance_ids:
            if set(provenance_ids).issubset(accessible_knowledge_base_ids):
                visible.append(to_conversation_read(conversation))
            continue
        try:
            ensure_conversation_history_access(db, user_id, conversation)
        except HTTPException as exc:
            if exc.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
                continue
            raise
        visible.append(to_conversation_read(conversation))
    return visible


def get_conversation(db: Session, user_id: str, conversation_id: str) -> Conversation:
    conversation = get_owned_conversation(db, user_id, conversation_id)
    ensure_conversation_history_access(db, user_id, conversation)
    return conversation


def get_owned_conversation(db: Session, user_id: str, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def ensure_conversation_history_access(db: Session, user_id: str, conversation: Conversation) -> None:
    if not conversation.history_provenance_complete:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation history is unavailable")
    stored_knowledge_base_ids = conversation_provenance_ids(conversation)
    if stored_knowledge_base_ids:
        for knowledge_base_id in stored_knowledge_base_ids:
            ensure_kb_access(db, user_id, knowledge_base_id, required_role="viewer")
        return

    runs = db.scalars(select(AgentRun).where(AgentRun.conversation_id == conversation.id)).all()
    logs = db.scalars(select(RetrievalLog).where(RetrievalLog.conversation_id == conversation.id)).all()
    if not runs and not logs:
        resolve_search_scope(
            db,
            user_id,
            conversation.knowledge_base_id,
            scope_type=conversation.search_scope,
            department_id=conversation.search_department_id,
        )
        return
    for run in runs:
        ensure_agent_run_access(db, user_id, run)
    for log in logs:
        ensure_retrieval_log_access(db, user_id, log)


def conversation_provenance_ids(conversation: Conversation) -> list[str]:
    knowledge_base_ids = [
        value
        for value in (conversation.searched_knowledge_base_ids or [])
        if isinstance(value, str) and value
    ]
    if conversation.knowledge_base_id:
        knowledge_base_ids.append(conversation.knowledge_base_id)
    return list(dict.fromkeys(knowledge_base_ids))


def get_conversation_detail(db: Session, user_id: str, conversation_id: str) -> ConversationRead:
    return to_conversation_read(get_conversation(db, user_id, conversation_id))


def delete_conversation(db: Session, user_id: str, conversation_id: str) -> None:
    conversation = get_owned_conversation(db, user_id, conversation_id)
    title = conversation.title
    knowledge_base_id = conversation.knowledge_base_id
    search_scope = conversation.search_scope
    db.delete(conversation)
    db.commit()
    try:
        short_term.clear_short_term_memory(user_id, conversation_id)
    except Exception:
        pass
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
    conversation_lease: ConversationRunLease | None = None
    stream_slot: AgentStreamSlot | None = None
    question = payload.question.strip()
    if not question:
        yield sse_event("error", {"message": "Question cannot be empty"})
        return

    if payload.memory_mode == "off":
        use_memory_for_turn = False
    elif payload.memory_mode == "normal":
        use_memory_for_turn = True
    else:
        use_memory_for_turn = not should_skip_memory_for_turn(question)
    try:
        conversation = get_conversation(db, user_id, conversation_id)
        try:
            conversation_lease = acquire_conversation_run_lease(conversation.id)
        except HTTPException as exc:
            yield sse_event(
                "error",
                {
                    "message": str(exc.detail),
                    "code": "conversation_coordination_unavailable",
                    "status_code": exc.status_code,
                },
            )
            return
        if conversation_lease is None:
            yield sse_event(
                "error",
                {
                    "message": "Conversation already has an active response",
                    "code": "conversation_busy",
                    "status_code": status.HTTP_409_CONFLICT,
                },
            )
            return
        stream_slot = acquire_agent_stream_slot()
        if stream_slot is None:
            yield sse_event(
                "error",
                {
                    "message": "Agent response capacity is temporarily exhausted",
                    "code": "agent_capacity_exhausted",
                    "status_code": status.HTTP_503_SERVICE_UNAVAILABLE,
                },
            )
            return
        if conversation.title == "新会话":
            conversation.title = title_from_question(question)

        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=question,
            status="completed",
            memory_enabled=use_memory_for_turn,
        )
        conversation.updated_at = datetime.now(timezone.utc)
        db.add_all([conversation, user_message])
        db.commit()
        db.refresh(conversation)
        db.refresh(user_message)

        yield sse_event("conversation", to_conversation_read(conversation).model_dump(mode="json"))
        yield sse_event("user_message", to_message_read(user_message).model_dump(mode="json"))
        if use_memory_for_turn:
            append_short_term_memory(user_id, conversation.id, "user", question)
        else:
            yield sse_event(
                "trace",
                {
                    "node": "memory_policy",
                    "status": "skipped",
                    "reason": "user requested no memory for this turn",
                },
            )

        yield sse_event("trace", {"node": "agent_runtime", "status": "started"})
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
            memory_enabled=use_memory_for_turn,
            stream_slot=stream_slot,
        ):
            if stream_event["type"] == "token":
                token_emitted = True
                yield sse_event("token", {"content": stream_event["content"]})
            elif stream_event["type"] == "done":
                agent_run = stream_event["agent_run"]
                token_emitted = bool(stream_event["token_emitted"])
        if agent_run is None:
            raise RuntimeError("Agent run did not complete")
        yield sse_event("trace", {"node": "agent_runtime", "status": agent_run.status})
        citations = [CitationRead(**citation) for citation in agent_run.citations]
        answer = agent_run.answer
        yield sse_event("agent_run", to_agent_run_read(agent_run).model_dump(mode="json"))
        retrieval_logs = get_run_retrieval_logs(db, agent_run)
        for retrieval_log in retrieval_logs:
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
            memory_enabled=use_memory_for_turn,
        )
        conversation.updated_at = datetime.now(timezone.utc)
        db.add_all([conversation, assistant_message])
        db.commit()
        db.refresh(assistant_message)
        agent_run = attach_agent_run_to_message(db, agent_run, assistant_message.id)
        retrieval_logs = [
            attach_retrieval_log_to_message(db, retrieval_log, assistant_message.id)
            for retrieval_log in retrieval_logs
        ]
        try:
            agent_run = apply_deferred_memory_update(
                db,
                agent_run,
                source_message_id=user_message.id,
            )
        except Exception:
            db.rollback()
        if use_memory_for_turn:
            append_short_term_memory(user_id, conversation.id, "assistant", answer)
            maybe_update_conversation_summary(db, user_id, conversation, question, answer)

        yield sse_event("assistant_message", to_message_read(assistant_message).model_dump(mode="json"))
        yield sse_event("agent_run", to_agent_run_read(agent_run).model_dump(mode="json"))
        for retrieval_log in retrieval_logs:
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
                memory_enabled=use_memory_for_turn,
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
        if stream_slot is not None:
            stream_slot.release_request()
        if conversation_lease is not None:
            release_conversation_run_lease(conversation_lease)


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
    timeout_seconds: float | None = None,
    memory_enabled: bool = True,
    stream_slot: AgentStreamSlot | None = None,
) -> Iterator[dict]:
    cancel_event = cancel_event or Event()
    timeout_seconds = agent_stream_timeout_seconds() if timeout_seconds is None else max(timeout_seconds, 0.001)
    deadline = monotonic() + timeout_seconds
    queue: Queue[tuple[str, object | None]] = Queue(maxsize=AGENT_STREAM_QUEUE_MAXSIZE)
    WorkerSession = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)
    stream_slot = stream_slot or acquire_agent_stream_slot()
    if stream_slot is None:
        raise RuntimeError("Agent response capacity is temporarily exhausted")

    def put_event(event: str, value: object | None) -> bool:
        while not cancel_event.is_set():
            try:
                queue.put((event, value), timeout=0.1)
                return True
            except Full:
                continue
        return False

    def ensure_stream_active() -> None:
        if monotonic() >= deadline:
            cancel_event.set()
            raise AgentRunTimeout(f"Agent run timed out after {timeout_seconds:g} seconds")
        if cancel_event.is_set():
            raise AgentRunCancelled("Agent run cancelled")

    def enqueue_token(token: str) -> None:
        ensure_stream_active()
        if token:
            if not put_event("token", token):
                raise AgentRunCancelled("Agent run cancelled")

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
                cancel_event=cancel_event,
                deadline_monotonic=deadline,
                defer_memory_update=True,
                memory_enabled=memory_enabled,
            )
            put_event("agent_run_id", run.id)
        except AgentRunCancelled as exc:
            worker_db.rollback()
            put_event("cancelled", str(exc))
        except AgentRunTimeout as exc:
            worker_db.rollback()
            put_event("error", str(exc))
        except Exception as exc:
            worker_db.rollback()
            put_event("error", str(exc))
        finally:
            worker_db.close()
            put_event("done", None)
            stream_slot.release_worker()

    thread = Thread(target=worker, daemon=True)
    if not stream_slot.transfer_to_worker():
        raise RuntimeError("Agent response capacity slot is no longer available")
    try:
        thread.start()
    except Exception:
        stream_slot.release_worker()
        raise

    agent_run_id: str | None = None
    error_message = ""
    token_emitted = False
    try:
        while True:
            ensure_stream_active()
            remaining = max(deadline - monotonic(), 0.001)
            try:
                event, value = queue.get(timeout=min(0.25, remaining))
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

        thread.join(timeout=1)
        if error_message:
            raise RuntimeError(error_message)
        if not agent_run_id:
            raise RuntimeError("Agent run did not return an id")
        agent_run = db.get(AgentRun, agent_run_id)
        if agent_run is None:
            raise RuntimeError("Agent run did not complete")
        yield {"type": "done", "agent_run": agent_run, "token_emitted": token_emitted}
    finally:
        if thread.is_alive():
            cancel_event.set()
            thread.join(timeout=0.5)


def acquire_agent_stream_slot() -> AgentStreamSlot | None:
    global _AGENT_STREAM_ACTIVE
    limit = get_settings().agent_stream_max_concurrency
    slot = AgentStreamSlot()
    with _AGENT_STREAM_CAPACITY_GUARD:
        if _AGENT_STREAM_ACTIVE >= limit:
            return None
        _AGENT_STREAM_ACTIVE += 1
    return slot


def release_agent_stream_capacity() -> None:
    global _AGENT_STREAM_ACTIVE
    with _AGENT_STREAM_CAPACITY_GUARD:
        _AGENT_STREAM_ACTIVE = max(0, _AGENT_STREAM_ACTIVE - 1)


def agent_stream_timeout_seconds() -> float:
    return max(
        AGENT_STREAM_MIN_TIMEOUT_SECONDS,
        float(get_settings().llm_timeout_seconds * AGENT_STREAM_TIMEOUT_LLM_CALLS),
    )


def acquire_conversation_run_lease(
    conversation_id: str,
    *,
    lease_seconds: int | None = None,
) -> ConversationRunLease | None:
    key = f"agent:conversation:{conversation_id}:lease"
    token = uuid4().hex
    ttl = lease_seconds or int(agent_stream_timeout_seconds() + CONVERSATION_LEASE_GRACE_SECONDS)
    redis_client = short_term.get_redis_client()
    if redis_client is not None:
        try:
            acquired = redis_client.set(key, token, nx=True, ex=max(ttl, 1))
        except Exception as exc:
            if get_settings().app_env.strip().lower() in PRODUCTION_ENVS:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Conversation coordination is temporarily unavailable",
                ) from exc
            redis_client = None
        else:
            if acquired:
                return ConversationRunLease(key=key, token=token, redis_client=redis_client)
            return None
    elif get_settings().app_env.strip().lower() in PRODUCTION_ENVS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conversation coordination is temporarily unavailable",
        )

    with _PROCESS_CONVERSATION_LOCKS_GUARD:
        process_lock = _PROCESS_CONVERSATION_LOCKS.setdefault(key, Lock())
        if not process_lock.acquire(blocking=False):
            return None
        return ConversationRunLease(key=key, token=token, process_lock=process_lock)


def release_conversation_run_lease(lease: ConversationRunLease) -> None:
    if lease.redis_client is not None:
        try:
            lease.redis_client.eval(CONVERSATION_LEASE_RELEASE_SCRIPT, 1, lease.key, lease.token)
        except Exception:
            pass
        return

    if lease.process_lock is None:
        return
    with _PROCESS_CONVERSATION_LOCKS_GUARD:
        if lease.process_lock.locked():
            lease.process_lock.release()
        if _PROCESS_CONVERSATION_LOCKS.get(lease.key) is lease.process_lock:
            _PROCESS_CONVERSATION_LOCKS.pop(lease.key, None)


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
        memory_enabled=message.memory_enabled,
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
    try:
        ensure_summary_dispatcher_started()
        _SUMMARY_DISPATCH_QUEUE.put_nowait((conversation.id, user_id))
    except Exception:
        return


def ensure_summary_dispatcher_started() -> None:
    global _SUMMARY_DISPATCH_THREAD

    if _SUMMARY_DISPATCH_THREAD is not None and _SUMMARY_DISPATCH_THREAD.is_alive():
        return
    with _SUMMARY_DISPATCH_THREAD_GUARD:
        if _SUMMARY_DISPATCH_THREAD is not None and _SUMMARY_DISPATCH_THREAD.is_alive():
            return
        _SUMMARY_DISPATCH_THREAD = Thread(target=summary_dispatch_loop, daemon=True)
        _SUMMARY_DISPATCH_THREAD.start()


def summary_dispatch_loop() -> None:
    while True:
        conversation_id, user_id = _SUMMARY_DISPATCH_QUEUE.get()
        try:
            from app.workers.memory_tasks import update_conversation_summary_task

            update_conversation_summary_task.delay(conversation_id, user_id)
        except Exception:
            pass
        finally:
            _SUMMARY_DISPATCH_QUEUE.task_done()


def get_run_retrieval_logs(db: Session, run: AgentRun) -> list[RetrievalLog]:
    retrieval_log_ids = list((run.state or {}).get("retrieval_log_ids") or [])
    if run.retrieval_log_id:
        retrieval_log_ids.append(run.retrieval_log_id)
    return [
        log
        for log_id in dict.fromkeys(retrieval_log_ids)
        if isinstance(log_id, str) and (log := db.get(RetrievalLog, log_id)) is not None
    ]


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

from __future__ import annotations

import unittest
from queue import Queue as StandardQueue
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agents.runtime import run_agent_turn
from app.agents.state import AgentRunState, AgentRunCancelled, AgentRunTimeout
from app.db.models.agent_run import AgentRun
from app.db.models.conversation import Conversation, Message
from app.db.models.knowledge_base import KnowledgeBaseMember
from app.db.models.retrieval_log import RetrievalLog
from app.db.models.user import User
from app.schemas.agent import AgentRunRequest, AgentTraceStep
from app.schemas.conversation import ConversationCreate, StreamMessageRequest
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.schemas.qa import CitationRead
from app.services.agent_service import get_agent_run, list_agent_runs, run_agent
from app.services.conversation_service import (
    AGENT_STREAM_QUEUE_MAXSIZE,
    acquire_agent_stream_slot,
    acquire_conversation_run_lease,
    create_conversation,
    delete_conversation,
    get_conversation_detail,
    list_conversations,
    release_conversation_run_lease,
    run_agent_streaming,
    stream_message_response,
)
from app.services.knowledge_base_service import create_knowledge_base
from app.services.retrieval_log_service import get_retrieval_log, list_retrieval_logs
from helpers import create_user, isolated_session


class FakeLeaseRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, script: str, key_count: int, key: str, token: str) -> int:
        if self.values.get(key) != token:
            return 0
        self.values.pop(key, None)
        return 1


class AgentRuntimeRegressionTests(unittest.TestCase):
    def test_legacy_trace_and_optional_citation_fields_remain_readable(self) -> None:
        trace = AgentTraceStep.model_validate({"node": "load_memory", "status": "success"})
        citation = CitationRead.model_validate(
            {
                "chunk_id": "chunk",
                "document_id": "document",
                "file_name": "policy.pdf",
                "chunk_index": 0,
                "score": 0.9,
                "content_preview": "preview",
            }
        )

        self.assertEqual(trace.action, "")
        self.assertEqual(trace.input, {})
        self.assertIsNone(citation.title_path)
        self.assertIsNone(citation.page_number)

    def test_agent_request_rejects_unknown_search_scope(self) -> None:
        with self.assertRaises(ValidationError):
            AgentRunRequest(input="question", search_scope="unknown")

    def test_run_agent_validates_scope_before_runtime(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "invalid-scope@example.com", "Invalid Scope")

            with patch("app.services.agent_service.run_agent_turn") as runtime:
                with self.assertRaises(HTTPException) as raised:
                    run_agent(session, user.id, None, "question", search_scope="unknown")

            self.assertEqual(raised.exception.status_code, 400)
            runtime.assert_not_called()

    def test_failed_runtime_rolls_back_before_persisting_failed_run(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "failed-run@example.com", "Failed Run")

            def leave_failed_transaction(db, state: AgentRunState) -> AgentRunState:
                db.add(
                    User(
                        email=user.email,
                        username="Duplicate",
                        hashed_password=user.hashed_password,
                    )
                )
                try:
                    db.flush()
                except IntegrityError:
                    pass
                state.status = "failed"
                state.error_message = "runtime failed"
                return state

            with patch("app.services.agent_service.run_agent_turn", side_effect=leave_failed_transaction):
                with self.assertRaisesRegex(RuntimeError, "runtime failed"):
                    run_agent(
                        session,
                        user.id,
                        None,
                        "question",
                        search_scope="accessible",
                    )

            failed_run = session.scalar(select(AgentRun).where(AgentRun.user_id == user.id))
            self.assertIsNotNone(failed_run)
            self.assertEqual(failed_run.status, "failed")
            self.assertEqual(failed_run.error_message, "runtime failed")

    def test_runtime_preserves_http_errors_and_cancellation(self) -> None:
        state = AgentRunState(user_id="user", knowledge_base_id=None, input="question")
        http_error = HTTPException(status_code=403, detail="forbidden")

        with patch("app.agents.runtime.load_core_memory_context", side_effect=http_error):
            with self.assertRaises(HTTPException) as raised:
                run_agent_turn(Mock(), state)
        self.assertEqual(raised.exception.status_code, 403)

        cancel_event = Event()
        cancel_event.set()
        cancelled_state = AgentRunState(
            user_id="user",
            knowledge_base_id=None,
            input="question",
            cancel_event=cancel_event,
        )
        with patch("app.agents.runtime.load_core_memory_context") as load_context:
            with self.assertRaises(AgentRunCancelled):
                run_agent_turn(Mock(), cancelled_state)
        load_context.assert_not_called()

    def test_stream_timeout_is_bounded_and_cancels_worker(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "stream-timeout@example.com", "Stream Timeout")
            cancel_event = Event()
            worker_stopped = Event()
            queue_sizes: list[int] = []

            def queue_factory(maxsize: int = 0):
                queue_sizes.append(maxsize)
                return StandardQueue(maxsize=maxsize)

            def wait_for_cancel(*args, **kwargs):
                kwargs["cancel_event"].wait(1)
                worker_stopped.set()
                raise AgentRunCancelled("cancelled")

            with (
                patch("app.services.conversation_service.Queue", side_effect=queue_factory),
                patch("app.services.conversation_service.run_agent", side_effect=wait_for_cancel),
            ):
                with self.assertRaises(AgentRunTimeout):
                    list(
                        run_agent_streaming(
                            session,
                            user.id,
                            None,
                            "question",
                            top_k=None,
                            search_scope="accessible",
                            department_id=None,
                            conversation_id="conversation",
                            cancel_event=cancel_event,
                            timeout_seconds=0.05,
                        )
                    )

            self.assertEqual(queue_sizes, [AGENT_STREAM_QUEUE_MAXSIZE])
            self.assertTrue(cancel_event.is_set())
            self.assertTrue(worker_stopped.wait(1))

    def test_closing_stream_generator_signals_worker_cancellation(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "stream-close@example.com", "Stream Close")
            cancel_event = Event()
            worker_stopped = Event()

            def stream_until_cancel(*args, **kwargs):
                kwargs["on_token"]("first")
                kwargs["cancel_event"].wait(1)
                worker_stopped.set()
                raise AgentRunCancelled("cancelled")

            with patch("app.services.conversation_service.run_agent", side_effect=stream_until_cancel):
                stream = run_agent_streaming(
                    session,
                    user.id,
                    None,
                    "question",
                    top_k=None,
                    search_scope="accessible",
                    department_id=None,
                    conversation_id="conversation",
                    cancel_event=cancel_event,
                    timeout_seconds=2,
                )
                self.assertEqual(next(stream), {"type": "token", "content": "first"})
                stream.close()

            self.assertTrue(cancel_event.is_set())
            self.assertTrue(worker_stopped.wait(1))

    def test_process_fallback_lease_rejects_a_parallel_turn(self) -> None:
        with patch("app.services.conversation_service.short_term.get_redis_client", return_value=None):
            first = acquire_conversation_run_lease("conversation", lease_seconds=5)
            second = acquire_conversation_run_lease("conversation", lease_seconds=5)
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            release_conversation_run_lease(first)
            third = acquire_conversation_run_lease("conversation", lease_seconds=5)
            self.assertIsNotNone(third)
            release_conversation_run_lease(third)

    def test_production_conversation_lease_fails_closed_when_redis_is_unavailable(self) -> None:
        with (
            patch("app.services.conversation_service.short_term.get_redis_client", return_value=None),
            patch(
                "app.services.conversation_service.get_settings",
                return_value=SimpleNamespace(app_env="prod"),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                acquire_conversation_run_lease("conversation", lease_seconds=5)

        self.assertEqual(raised.exception.status_code, 503)

    def test_redis_lease_release_only_deletes_the_owners_token(self) -> None:
        redis = FakeLeaseRedis()
        with patch("app.services.conversation_service.short_term.get_redis_client", return_value=redis):
            lease = acquire_conversation_run_lease("conversation", lease_seconds=5)
            self.assertIsNotNone(lease)
            self.assertIsNone(acquire_conversation_run_lease("conversation", lease_seconds=5))

            redis.values[lease.key] = "replacement-owner"
            release_conversation_run_lease(lease)

        self.assertEqual(redis.values[lease.key], "replacement-owner")

    def test_busy_conversation_returns_conflict_without_writing_a_turn(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "busy-turn@example.com", "Busy Turn")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Busy KB", visibility="private"),
            )
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(knowledge_base_id=kb.id),
            )

            with patch("app.services.conversation_service.acquire_conversation_run_lease", return_value=None):
                body = "".join(
                    stream_message_response(
                        session,
                        user.id,
                        conversation.id,
                        StreamMessageRequest(question="question"),
                    )
                )

            self.assertIn('"code": "conversation_busy"', body)
            self.assertIn('"status_code": 409', body)
            self.assertEqual(
                session.scalar(select(Message).where(Message.conversation_id == conversation.id)),
                None,
            )

    def test_agent_stream_capacity_is_bounded_and_reusable(self) -> None:
        with patch(
            "app.services.conversation_service.get_settings",
            return_value=SimpleNamespace(agent_stream_max_concurrency=1),
        ):
            first = acquire_agent_stream_slot()
            self.assertIsNotNone(first)
            try:
                self.assertIsNone(acquire_agent_stream_slot())
            finally:
                first.release_request()

            second = acquire_agent_stream_slot()
            self.assertIsNotNone(second)
            second.release_request()

    def test_agent_capacity_exhaustion_rejects_before_writing_a_turn(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "capacity-turn@example.com", "Capacity Turn")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Capacity KB", visibility="private"),
            )
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(knowledge_base_id=kb.id),
            )

            with patch("app.services.conversation_service.acquire_agent_stream_slot", return_value=None):
                body = "".join(
                    stream_message_response(
                        session,
                        user.id,
                        conversation.id,
                        StreamMessageRequest(question="question"),
                    )
                )

            self.assertIn('"code": "agent_capacity_exhausted"', body)
            self.assertIn('"status_code": 503', body)
            self.assertEqual(
                session.scalar(select(Message).where(Message.conversation_id == conversation.id)),
                None,
            )

    def test_closing_outer_sse_generator_releases_conversation_lease(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "lease-close@example.com", "Lease Close")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Lease Close KB", visibility="private"),
            )
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(knowledge_base_id=kb.id),
            )
            lease = SimpleNamespace(key="lease", token="token")

            with (
                patch("app.services.conversation_service.acquire_conversation_run_lease", return_value=lease),
                patch("app.services.conversation_service.release_conversation_run_lease") as release_lease,
            ):
                stream = stream_message_response(
                    session,
                    user.id,
                    conversation.id,
                    StreamMessageRequest(question="question"),
                )
                self.assertIn("event: conversation", next(stream))
                stream.close()

            release_lease.assert_called_once_with(lease)

    def test_agent_run_records_and_rechecks_every_searched_knowledge_base(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "range-owner@example.com", "Range Owner")
            first = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="First", visibility="private"),
            )
            second = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Second", visibility="private"),
            )

            def complete_runtime(_db, state: AgentRunState) -> AgentRunState:
                retrieval_log = RetrievalLog(
                    user_id=state.user_id,
                    scope_type="accessible",
                    searched_knowledge_base_ids=[first.id, second.id],
                    query=state.input,
                )
                _db.add(retrieval_log)
                _db.commit()
                _db.refresh(retrieval_log)
                state.retrieval_log_id = retrieval_log.id
                state.retrieval_log_ids = [retrieval_log.id]
                state.rag_searched = True
                state.status = "completed"
                state.answer = "answer"
                return state

            with patch("app.services.agent_service.run_agent_turn", side_effect=complete_runtime):
                run = run_agent(
                    session,
                    user.id,
                    None,
                    "question",
                    search_scope="accessible",
                )

            self.assertEqual(
                set(run.state["searched_knowledge_base_ids"]),
                {first.id, second.id},
            )
            read = get_agent_run(session, user.id, run.id)
            self.assertEqual(set(read.searched_knowledge_base_ids), {first.id, second.id})

    def test_chat_run_does_not_claim_unsearched_accessible_knowledge_bases(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "chat-scope-owner@example.com", "Chat Scope Owner")
            viewer = create_user(session, "chat-scope-viewer@example.com", "Chat Scope Viewer")
            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Chat Scope KB", visibility="private"),
            )
            membership = KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=viewer.id, role="viewer")
            session.add(membership)
            session.commit()

            def complete_chat(_db, state: AgentRunState) -> AgentRunState:
                state.status = "completed"
                state.answer = "hello"
                return state

            with patch("app.services.agent_service.run_agent_turn", side_effect=complete_chat):
                run = run_agent(
                    session,
                    viewer.id,
                    None,
                    "hello",
                    search_scope="accessible",
                )

            self.assertEqual(run.state["searched_knowledge_base_ids"], [])
            session.delete(membership)
            session.commit()
            self.assertEqual(get_agent_run(session, viewer.id, run.id).answer, "hello")

    def test_completed_rag_search_requires_retrieval_provenance(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "missing-provenance@example.com", "Missing Provenance")

            def incomplete_rag(_db, state: AgentRunState) -> AgentRunState:
                state.rag_searched = True
                state.status = "completed"
                state.answer = "unsupported answer"
                return state

            with (
                patch("app.services.agent_service.run_agent_turn", side_effect=incomplete_rag),
                self.assertRaises(RuntimeError) as raised,
            ):
                run_agent(
                    session,
                    user.id,
                    None,
                    "question",
                    search_scope="accessible",
                )

            self.assertIn("provenance", str(raised.exception).lower())
            failed_run = session.scalar(
                select(AgentRun).where(AgentRun.user_id == user.id, AgentRun.status == "failed")
            )
            self.assertIsNotNone(failed_run)
            self.assertEqual(failed_run.answer, "")
            self.assertEqual(failed_run.citations, [])

    def test_historical_run_is_denied_after_one_searched_kb_is_revoked(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "range-admin@example.com", "Range Admin")
            viewer = create_user(session, "range-viewer@example.com", "Range Viewer")
            first = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="First Private", visibility="private"),
            )
            second = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Second Private", visibility="private"),
            )
            memberships = [
                KnowledgeBaseMember(knowledge_base_id=first.id, user_id=viewer.id, role="viewer"),
                KnowledgeBaseMember(knowledge_base_id=second.id, user_id=viewer.id, role="viewer"),
            ]
            session.add_all(memberships)
            session.commit()
            run = AgentRun(
                user_id=viewer.id,
                knowledge_base_id=None,
                input="question",
                status="completed",
                answer="sensitive answer",
                state={"searched_knowledge_base_ids": [first.id, second.id]},
            )
            session.add(run)
            session.commit()
            session.refresh(run)

            get_agent_run(session, viewer.id, run.id)
            session.delete(memberships[1])
            session.commit()

            with self.assertRaises(HTTPException) as raised:
                get_agent_run(session, viewer.id, run.id)
            self.assertIn(raised.exception.status_code, {403, 404})

    def test_legacy_multi_scope_run_without_provenance_is_hidden(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "legacy-run@example.com", "Legacy Run")
            run = AgentRun(
                user_id=user.id,
                knowledge_base_id=None,
                input="question",
                status="completed",
                answer="legacy multi-scope answer",
                state={"search_scope": "accessible"},
            )
            session.add(run)
            session.commit()
            session.refresh(run)

            with self.assertRaises(HTTPException) as raised:
                get_agent_run(session, user.id, run.id)
            self.assertEqual(raised.exception.status_code, 404)

    def test_legacy_multi_scope_retrieval_log_derives_and_rechecks_chunk_provenance(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "legacy-log-owner@example.com", "Legacy Log Owner")
            viewer = create_user(session, "legacy-log-viewer@example.com", "Legacy Log Viewer")
            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Legacy Log KB", visibility="private"),
            )
            membership = KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=viewer.id, role="viewer")
            log = RetrievalLog(
                user_id=viewer.id,
                knowledge_base_id=None,
                scope_type="accessible",
                searched_knowledge_base_ids=[],
                query="question",
                selected_chunks=[
                    {
                        "knowledge_base_id": kb.id,
                        "content_preview": "LEGACY_SECRET_PREVIEW",
                    }
                ],
            )
            session.add_all([membership, log])
            session.commit()
            session.refresh(log)

            self.assertEqual(
                get_retrieval_log(session, viewer.id, log.id).selected_chunks[0]["content_preview"],
                "LEGACY_SECRET_PREVIEW",
            )
            session.delete(membership)
            session.commit()

            with self.assertRaises(HTTPException) as raised:
                get_retrieval_log(session, viewer.id, log.id)
            self.assertIn(raised.exception.status_code, {403, 404})

    def test_legacy_multi_scope_retrieval_log_with_unattributed_preview_is_hidden(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "legacy-unattributed@example.com", "Legacy Unattributed")
            log = RetrievalLog(
                user_id=user.id,
                knowledge_base_id=None,
                scope_type="accessible",
                searched_knowledge_base_ids=[],
                query="question",
                selected_chunks=[{"content_preview": "UNATTRIBUTED_SECRET_PREVIEW"}],
            )
            session.add(log)
            session.commit()
            session.refresh(log)

            with self.assertRaises(HTTPException) as raised:
                get_retrieval_log(session, user.id, log.id)
            self.assertEqual(raised.exception.status_code, 404)

    def test_revoked_history_is_filtered_without_breaking_visible_lists(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "filter-owner@example.com", "Filter Owner")
            viewer = create_user(session, "filter-viewer@example.com", "Filter Viewer")
            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Filtered KB", visibility="private"),
            )
            membership = KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=viewer.id, role="viewer")
            hidden_run = AgentRun(
                user_id=viewer.id,
                input="hidden question",
                status="completed",
                answer="hidden answer",
                state={"searched_knowledge_base_ids": [kb.id]},
            )
            visible_run = AgentRun(
                user_id=viewer.id,
                input="hello",
                status="completed",
                answer="hello",
                state={"searched_knowledge_base_ids": []},
            )
            hidden_log = RetrievalLog(
                user_id=viewer.id,
                scope_type="accessible",
                searched_knowledge_base_ids=[kb.id],
                query="hidden question",
            )
            visible_log = RetrievalLog(
                user_id=viewer.id,
                scope_type="accessible",
                searched_knowledge_base_ids=[],
                query="empty search",
            )
            session.add_all([membership, hidden_run, visible_run, hidden_log, visible_log])
            session.commit()
            session.delete(membership)
            session.commit()

            runs = list_agent_runs(session, viewer.id, limit=10)
            logs = list_retrieval_logs(session, viewer.id)

            self.assertEqual([run.id for run in runs], [visible_run.id])
            self.assertEqual([log.id for log in logs], [visible_log.id])

    def test_revoked_multi_scope_history_is_hidden_from_conversations_and_retrieval_logs(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "history-owner@example.com", "History Owner")
            viewer = create_user(session, "history-viewer@example.com", "History Viewer")
            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Revocable KB", visibility="private"),
            )
            membership = KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=viewer.id, role="viewer")
            conversation = Conversation(
                user_id=viewer.id,
                title="Revocable history",
                summary="SECRET_MULTI_SUMMARY",
                search_scope="accessible",
                searched_knowledge_base_ids=[kb.id],
            )
            session.add_all([membership, conversation])
            session.commit()
            session.refresh(conversation)
            run = AgentRun(
                user_id=viewer.id,
                conversation_id=conversation.id,
                input="question",
                status="completed",
                answer="answer",
                state={"searched_knowledge_base_ids": [kb.id]},
            )
            retrieval_log = RetrievalLog(
                user_id=viewer.id,
                knowledge_base_id=None,
                conversation_id=None,
                scope_type="accessible",
                searched_knowledge_base_ids=[kb.id],
                query="question",
                selected_chunks=[{"preview": "SECRET_CHUNK"}],
            )
            session.add_all([run, retrieval_log])
            session.commit()
            session.refresh(retrieval_log)

            self.assertEqual(get_conversation_detail(session, viewer.id, conversation.id).summary, "SECRET_MULTI_SUMMARY")
            self.assertEqual(get_retrieval_log(session, viewer.id, retrieval_log.id).selected_chunks[0]["preview"], "SECRET_CHUNK")

            session.delete(membership)
            session.delete(run)
            session.commit()

            with self.assertRaises(HTTPException):
                get_conversation_detail(session, viewer.id, conversation.id)
            self.assertNotIn(
                "SECRET_MULTI_SUMMARY",
                [item.summary for item in list_conversations(session, viewer.id)],
            )
            with self.assertRaises(HTTPException):
                get_retrieval_log(session, viewer.id, retrieval_log.id)

    def test_agent_run_listing_is_bounded_and_supports_offset(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "run-page@example.com", "Run Page")
            for index in range(3):
                session.add(
                    AgentRun(
                        user_id=user.id,
                        input=f"question {index}",
                        status="completed",
                        answer=f"answer {index}",
                        state={"searched_knowledge_base_ids": []},
                    )
                )
            session.commit()

            page = list_agent_runs(session, user.id, limit=1, offset=1)

            self.assertEqual(len(page), 1)

    def test_explicit_memory_off_skips_redis_and_summary_but_keeps_flagged_messages(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "temporary-turn@example.com", "Temporary Turn")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Temporary KB", visibility="private"),
            )
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(knowledge_base_id=kb.id),
            )

            def fake_run_agent(*args, **kwargs) -> AgentRun:
                worker_db = args[0]
                run = AgentRun(
                    user_id=args[1],
                    knowledge_base_id=args[2],
                    conversation_id=kwargs["conversation_id"],
                    input=args[3],
                    status="completed",
                    answer="temporary answer",
                    citations=[],
                    trace=[
                        {
                            "node": "memory_agent",
                            "action": "load_context_skipped",
                            "input": {},
                            "output": {"reason": "user requested no memory for this turn"},
                        }
                    ],
                    state={},
                )
                worker_db.add(run)
                worker_db.commit()
                worker_db.refresh(run)
                return run

            with (
                patch("app.services.conversation_service.run_agent", side_effect=fake_run_agent),
                patch("app.services.conversation_service.append_short_term_memory") as append_memory,
                patch("app.services.conversation_service.maybe_update_conversation_summary") as update_summary,
            ):
                body = "".join(
                    stream_message_response(
                        session,
                        user.id,
                        conversation.id,
                        StreamMessageRequest(question="ordinary private request", memory_mode="off"),
                    )
                )

            self.assertIn("event: done", body)
            append_memory.assert_not_called()
            update_summary.assert_not_called()
            messages = session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at, Message.id)
            ).all()
            self.assertEqual([message.role for message in messages], ["user", "assistant"])
            self.assertTrue(all(not message.memory_enabled for message in messages))

    def test_explicit_normal_mode_overrides_legacy_no_memory_text_markers(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "forced-memory@example.com", "Forced Memory")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Forced Memory KB", visibility="private"),
            )
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(knowledge_base_id=kb.id),
            )

            def fake_run_agent(*args, **kwargs) -> AgentRun:
                worker_db = args[0]
                run = AgentRun(
                    user_id=args[1],
                    knowledge_base_id=args[2],
                    conversation_id=kwargs["conversation_id"],
                    input=args[3],
                    status="completed",
                    answer="normal answer",
                    state={"memory_enabled": kwargs["memory_enabled"]},
                )
                worker_db.add(run)
                worker_db.commit()
                worker_db.refresh(run)
                return run

            with (
                patch("app.services.conversation_service.run_agent", side_effect=fake_run_agent),
                patch("app.services.conversation_service.append_short_term_memory") as append_memory,
                patch("app.services.conversation_service.maybe_update_conversation_summary") as update_summary,
                patch(
                    "app.services.conversation_service.apply_deferred_memory_update",
                    side_effect=lambda _db, run, **_kwargs: run,
                ),
            ):
                body = "".join(
                    stream_message_response(
                        session,
                        user.id,
                        conversation.id,
                        StreamMessageRequest(
                            question="explain the no memory architecture",
                            memory_mode="normal",
                        ),
                    )
                )

            self.assertIn("event: done", body)
            self.assertEqual(append_memory.call_count, 2)
            update_summary.assert_called_once()
            messages = session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at, Message.id)
            ).all()
            self.assertTrue(all(message.memory_enabled for message in messages))

    def test_delete_conversation_clears_short_term_memory_best_effort(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "clear-short@example.com", "Clear Short")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Clear Short KB", visibility="private"),
            )
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(knowledge_base_id=kb.id),
            )

            with patch(
                "app.services.conversation_service.short_term.clear_short_term_memory",
                side_effect=RuntimeError("redis offline"),
            ) as clear_memory:
                delete_conversation(session, user.id, conversation.id)

            clear_memory.assert_called_once_with(user.id, conversation.id)


if __name__ == "__main__":
    unittest.main()

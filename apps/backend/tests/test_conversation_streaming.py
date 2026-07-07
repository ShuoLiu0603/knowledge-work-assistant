from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import select

from app.db.models.agent_run import AgentRun
from app.db.models.audit_log import AuditLog
from app.db.models.conversation import Message
from app.db.models.llm_call_log import LlmCallLog
from app.llm.provider import LlmCompletion, LlmMessage, LlmProvider
from app.schemas.conversation import ConversationCreate, StreamMessageRequest
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.conversation_service import create_conversation, delete_conversation, message_token_usage, stream_message_response
from app.services.knowledge_base_service import create_knowledge_base
from helpers import create_user, isolated_session


class FakeTokenProvider(LlmProvider):
    provider_name = "openai_compatible"
    model_name = "fake-chat"

    def complete_with_metadata(self, messages: list[LlmMessage], temperature: float = 0.1) -> LlmCompletion:
        content = "The policy is approved. [1]"
        return LlmCompletion(
            content=content,
            provider=self.provider_name,
            model_name=self.model_name,
            prompt_tokens=10,
            completion_tokens=6,
            total_tokens=16,
            latency_ms=1,
            status="success",
        )


class ConversationStreamingTests(unittest.TestCase):
    def test_provider_base_emits_tokens_through_callback(self) -> None:
        tokens: list[str] = []

        completion = FakeTokenProvider().answer_question_with_metadata(
            "What is the policy?",
            "[1] 来源：policy.md，chunk #0\nThe policy is approved.",
            on_token=tokens.append,
        )

        self.assertEqual("".join(tokens), completion.content)
        self.assertIn("[1]", completion.content)

    def test_stream_message_response_emits_tokens_before_persisted_assistant_message(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "stream@example.com", "Stream")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Streaming KB", visibility="private"),
            )
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(knowledge_base_id=kb.id),
            )

            def fake_run_agent(*args, **kwargs) -> AgentRun:
                on_token = kwargs["on_token"]
                on_token("企业")
                on_token("级回答")
                db = args[0]
                run = AgentRun(
                    user_id=args[1],
                    knowledge_base_id=args[2],
                    conversation_id=kwargs["conversation_id"],
                    input=args[3],
                    intent="rag",
                    status="completed",
                    answer="企业级回答",
                    citations=[],
                    trace=[{"node": "rag_agent", "action": "stream", "input": {}, "output": {}}],
                    state={},
                )
                db.add(run)
                db.commit()
                db.refresh(run)
                return run

            with patch("app.services.conversation_service.run_agent", side_effect=fake_run_agent):
                body = "".join(
                    stream_message_response(
                        session,
                        user.id,
                        conversation.id,
                        StreamMessageRequest(question="请回答政策。"),
                    )
                )

            self.assertIn('event: token\ndata: {"content": "企业"}', body)
            self.assertLess(body.index("企业"), body.index("event: assistant_message"))
            self.assertIn("event: done", body)

            messages = session.scalars(
                select(Message).where(Message.conversation_id == conversation.id)
            ).all()
            user_messages = [message for message in messages if message.role == "user"]
            assistant_messages = [message for message in messages if message.role == "assistant"]
            self.assertEqual(len(user_messages), 1)
            self.assertEqual(len(assistant_messages), 1)
            self.assertEqual(assistant_messages[0].content, "企业级回答")

            run = session.scalar(select(AgentRun).where(AgentRun.conversation_id == conversation.id))
            self.assertIsNotNone(run)
            self.assertEqual(run.message_id, assistant_messages[0].id)

    def test_summary_failure_does_not_fail_streamed_answer(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "summary-failure@example.com", "Summary Failure")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Summary Failure KB", visibility="private"),
            )
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(knowledge_base_id=kb.id),
            )

            def fake_run_agent(*args, **kwargs) -> AgentRun:
                db = args[0]
                run = AgentRun(
                    user_id=args[1],
                    knowledge_base_id=args[2],
                    conversation_id=kwargs["conversation_id"],
                    input=args[3],
                    intent="rag",
                    status="completed",
                    answer="已完成回答",
                    citations=[],
                    trace=[],
                    state={},
                )
                db.add(run)
                db.commit()
                db.refresh(run)
                return run

            with (
                patch("app.services.conversation_service.run_agent", side_effect=fake_run_agent),
                patch("app.services.conversation_service.should_update_conversation_summary", return_value=True),
                patch("app.services.conversation_service.update_conversation_summary", side_effect=RuntimeError("summary offline")),
            ):
                body = "".join(
                    stream_message_response(
                        session,
                        user.id,
                        conversation.id,
                        StreamMessageRequest(question="请回答政策。"),
                    )
                )

            self.assertIn("event: done", body)
            self.assertNotIn("event: error", body)
            assistant_messages = session.scalars(
                select(Message).where(Message.conversation_id == conversation.id, Message.role == "assistant")
            ).all()
            self.assertEqual(len(assistant_messages), 1)
            self.assertEqual(assistant_messages[0].content, "已完成回答")

    def test_public_target_conversation_does_not_require_primary_knowledge_base(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "public-target@example.com", "Public Target")
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(search_scope="public"),
            )

            def fake_run_agent(*args, **kwargs) -> AgentRun:
                db = args[0]
                run = AgentRun(
                    user_id=args[1],
                    knowledge_base_id=args[2],
                    conversation_id=kwargs["conversation_id"],
                    input=args[3],
                    intent="rag",
                    status="completed",
                    answer="公共知识库回答",
                    citations=[],
                    trace=[],
                    state={"search_scope": kwargs["search_scope"]},
                )
                db.add(run)
                db.commit()
                db.refresh(run)
                return run

            with patch("app.services.conversation_service.run_agent", side_effect=fake_run_agent) as run_agent:
                body = "".join(
                    stream_message_response(
                        session,
                        user.id,
                        conversation.id,
                        StreamMessageRequest(question="请回答公共制度。"),
                    )
                )

            self.assertIsNone(conversation.knowledge_base_id)
            self.assertEqual(conversation.search_scope, "public")
            self.assertIn("event: done", body)
            self.assertIsNone(run_agent.call_args.args[2])
            self.assertEqual(run_agent.call_args.kwargs["search_scope"], "public")

    def test_delete_conversation_removes_messages(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "delete-conversation@example.com", "Delete Conversation")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Delete Conversation KB", visibility="private"),
            )
            conversation = create_conversation(
                session,
                user.id,
                ConversationCreate(knowledge_base_id=kb.id),
            )
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="Please answer.",
                    status="completed",
                )
            )
            session.commit()

            delete_conversation(session, user.id, conversation.id)

            self.assertEqual(session.scalar(select(Message).where(Message.conversation_id == conversation.id)), None)
            audit_log = session.scalar(select(AuditLog).where(AuditLog.action == "conversation.delete"))
            self.assertIsNotNone(audit_log)
            self.assertEqual(audit_log.resource_id, conversation.id)

    def test_message_token_usage_aggregates_agent_llm_logs(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "token-usage@example.com", "Token Usage")
            first = LlmCallLog(
                user_id=user.id,
                agent_name="supervisor",
                provider="openai_compatible",
                model_name="fake-chat",
                prompt_tokens=3,
                completion_tokens=1,
                total_tokens=4,
                latency_ms=10,
                status="success",
                fallback_used=False,
            )
            second = LlmCallLog(
                user_id=user.id,
                agent_name="rag_agent",
                provider="openai_compatible",
                model_name="fake-chat",
                prompt_tokens=7,
                completion_tokens=5,
                total_tokens=12,
                latency_ms=20,
                status="success",
                fallback_used=False,
            )
            session.add_all([first, second])
            session.commit()
            session.refresh(first)
            session.refresh(second)

            run = AgentRun(
                user_id=user.id,
                knowledge_base_id="kb",
                input="Question",
                intent="rag",
                status="completed",
                answer="Answer",
                state={"llm_log_id": second.id, "llm_log_ids": [first.id, second.id]},
            )

            usage = message_token_usage(session, run)

            self.assertEqual(usage["llm_log_id"], second.id)
            self.assertEqual(usage["llm_log_ids"], [first.id, second.id])
            self.assertEqual(usage["prompt_tokens"], 10)
            self.assertEqual(usage["completion_tokens"], 6)
            self.assertEqual(usage["total_tokens"], 16)
            self.assertEqual(usage["latency_ms"], 30)


if __name__ == "__main__":
    unittest.main()

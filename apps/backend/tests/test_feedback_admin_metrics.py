from __future__ import annotations

import unittest

from app.db.models.conversation import Conversation, Message
from app.db.models.llm_call_log import LlmCallLog
from app.db.models.retrieval_log import RetrievalLog
from app.schemas.feedback import FeedbackCreate
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.admin_service import get_admin_metrics
from app.services.feedback_service import create_feedback
from app.services.knowledge_base_service import create_knowledge_base
from helpers import create_user, isolated_session


class FeedbackAndAdminMetricTests(unittest.TestCase):
    def test_feedback_upsert_and_metrics(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "metrics@example.com", "Metrics")
            kb = create_knowledge_base(
                session,
                user.id,
                KnowledgeBaseCreate(name="Metrics KB", visibility="private"),
            )
            conversation = Conversation(user_id=user.id, knowledge_base_id=kb.id, title="Metrics conversation")
            session.add(conversation)
            session.flush()

            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="Answer",
                status="completed",
                token_usage={"total_tokens": 12},
                agent_trace=[{"node": "rag_agent"}],
            )
            session.add(assistant_message)
            session.flush()

            session.add(
                LlmCallLog(
                    user_id=user.id,
                    conversation_id=conversation.id,
                    agent_name="rag_agent",
                    provider="local",
                    model_name="local-rule-based",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    latency_ms=20,
                    status="success",
                    fallback_used=False,
                )
            )
            session.add(
                RetrievalLog(
                    user_id=user.id,
                    knowledge_base_id=kb.id,
                    conversation_id=conversation.id,
                    message_id=assistant_message.id,
                    question="question",
                    rewritten_query="question",
                    selected_chunks=[{"chunk_id": "chunk-1"}, {"chunk_id": "chunk-2"}],
                )
            )
            session.commit()

            positive = create_feedback(
                session,
                user.id,
                FeedbackCreate(message_id=assistant_message.id, rating=1),
            )
            updated = create_feedback(
                session,
                user.id,
                FeedbackCreate(message_id=assistant_message.id, rating=-1, reason="Missing details"),
            )

            self.assertEqual(positive.id, updated.id)
            self.assertEqual(updated.rating, -1)
            self.assertEqual(updated.reason, "Missing details")

            metrics = get_admin_metrics(session, user)

            self.assertEqual(metrics.conversation_count, 1)
            self.assertEqual(metrics.message_count, 1)
            self.assertEqual(metrics.llm_call_count, 1)
            self.assertEqual(metrics.total_tokens, 15)
            self.assertEqual(metrics.retrieval_log_count, 1)
            self.assertEqual(metrics.average_selected_chunks, 2)
            self.assertEqual(metrics.feedback_count, 1)
            self.assertEqual(metrics.negative_feedback_count, 1)
            self.assertEqual(metrics.positive_feedback_rate, 0.0)

    def test_metrics_are_scoped_for_users_and_global_for_admins(self) -> None:
        with isolated_session() as session:
            user_a = create_user(session, "metrics-a@example.com", "Metrics A")
            user_b = create_user(session, "metrics-b@example.com", "Metrics B")
            admin = create_user(session, "admin@example.com", "Admin")
            admin.is_admin = True
            session.add(admin)

            kb_a = create_knowledge_base(session, user_a.id, KnowledgeBaseCreate(name="Metrics A KB", visibility="private"))
            kb_b = create_knowledge_base(session, user_b.id, KnowledgeBaseCreate(name="Metrics B KB", visibility="private"))
            conversation_a = Conversation(user_id=user_a.id, knowledge_base_id=kb_a.id, title="A")
            conversation_b = Conversation(user_id=user_b.id, knowledge_base_id=kb_b.id, title="B")
            session.add_all([conversation_a, conversation_b])
            session.flush()

            session.add_all(
                [
                    LlmCallLog(
                        user_id=user_a.id,
                        conversation_id=conversation_a.id,
                        agent_name="rag_agent",
                        provider="local",
                        model_name="local-rule-based",
                        prompt_tokens=1,
                        completion_tokens=2,
                        total_tokens=3,
                        status="success",
                    ),
                    LlmCallLog(
                        user_id=user_b.id,
                        conversation_id=conversation_b.id,
                        agent_name="rag_agent",
                        provider="local",
                        model_name="local-rule-based",
                        prompt_tokens=4,
                        completion_tokens=5,
                        total_tokens=9,
                        status="success",
                    ),
                ]
            )
            session.commit()

            metrics_a = get_admin_metrics(session, user_a)
            metrics_admin = get_admin_metrics(session, admin)

            self.assertEqual(metrics_a.scope, "current_user")
            self.assertEqual(metrics_a.conversation_count, 1)
            self.assertEqual(metrics_a.llm_call_count, 1)
            self.assertEqual(metrics_a.total_tokens, 3)

            self.assertEqual(metrics_admin.scope, "global")
            self.assertEqual(metrics_admin.conversation_count, 2)
            self.assertEqual(metrics_admin.llm_call_count, 2)
            self.assertEqual(metrics_admin.total_tokens, 12)


if __name__ == "__main__":
    unittest.main()

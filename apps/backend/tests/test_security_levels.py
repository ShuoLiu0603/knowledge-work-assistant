from __future__ import annotations

import unittest
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import HTTPException

from app.db.models.document import Document, DocumentChunk
from app.db.models.knowledge_base import KnowledgeBaseMember
from app.db.models.audit_log import AuditLog
from app.llm.provider import LlmCompletion
from app.rag.advanced_retrieval import retrieve_bm25_routes
from app.rag.retrieval import RetrievedChunk
from app.rag.vector_store import search_knowledge_base_chunks
from app.schemas.admin import AdminUserUpdate
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.admin_service import list_admin_users, update_admin_user
from app.services.knowledge_base_service import create_knowledge_base
from app.services.qa_service import build_rag_answer
from helpers import create_user, isolated_session


class FakeRagLlmProvider:
    def answer_question_with_metadata(self, question: str, context: str, memory_context: str = "", on_token=None) -> LlmCompletion:
        content = "Answer from retrieved policy. [1]"
        if on_token:
            on_token(content)
        return LlmCompletion(
            content=content,
            provider="openai_compatible",
            model_name="fake-chat",
            prompt_tokens=10,
            completion_tokens=6,
            total_tokens=16,
            latency_ms=1,
            status="success",
        )


class SecurityLevelTests(unittest.TestCase):
    def test_bm25_retrieval_respects_security_level(self) -> None:
        with isolated_session() as session:
            owner = create_user(session, "security-owner@example.com", "Security Owner")
            low_user = create_user(session, "security-low@example.com", "Security Low")
            high_user = create_user(session, "security-high@example.com", "Security High")
            high_user.security_level = 4
            kb = create_knowledge_base(
                session,
                owner.id,
                KnowledgeBaseCreate(name="Security KB", visibility="private"),
            )
            session.add_all(
                [
                    KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=low_user.id, role="viewer"),
                    KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=high_user.id, role="viewer"),
                ]
            )
            low_document = make_document(kb.id, "public-policy.md", "hash-low", security_level=1)
            high_document = make_document(kb.id, "secret-policy.md", "hash-high", security_level=4)
            session.add_all([low_document, high_document])
            session.flush()
            session.add_all(
                [
                    make_chunk(low_document, "public reimbursement policy", security_level=1),
                    make_chunk(high_document, "secret executive reimbursement policy", security_level=4),
                ]
            )
            session.commit()

            low_routes = retrieve_bm25_routes(session, kb.id, ["reimbursement policy"], route_limit=10, max_security_level=1)
            high_routes = retrieve_bm25_routes(session, kb.id, ["reimbursement policy"], route_limit=10, max_security_level=4)

            low_bm25 = low_routes["bm25_original"]
            high_bm25 = high_routes["bm25_original"]

            self.assertEqual({item.chunk.file_name for item in low_bm25}, {"public-policy.md"})
            self.assertEqual({item.chunk.file_name for item in high_bm25}, {"public-policy.md", "secret-policy.md"})

    def test_vector_search_uses_single_collection_with_security_filter(self) -> None:
        captured_payloads = []

        class FakeEmbeddingProvider:
            def embed_text(self, _text: str) -> list[float]:
                return [0.1, 0.2, 0.3]

        def fake_qdrant_request(_method, _path, payload=None, **_kwargs):
            if payload is not None:
                captured_payloads.append(payload)
            return 200, {"result": []}

        with (
            patch("app.rag.vector_store.ensure_qdrant_collection"),
            patch("app.rag.vector_store.get_embedding_provider", return_value=FakeEmbeddingProvider()),
            patch("app.rag.vector_store.qdrant_request", side_effect=fake_qdrant_request),
        ):
            search_knowledge_base_chunks("owner-id", "kb-id", "policy", limit=3, max_security_level=2)

        search_payload = captured_payloads[-1]
        must_filters = search_payload["filter"]["must"]
        should_filters = search_payload["filter"]["should"]
        self.assertIn({"key": "knowledge_base_id", "match": {"value": "kb-id"}}, must_filters)
        self.assertIn({"key": "security_level", "range": {"lte": 2}}, should_filters)
        self.assertIn({"is_empty": {"key": "security_level"}}, should_filters)

    def test_admin_can_update_user_security_level(self) -> None:
        with isolated_session() as session:
            admin = create_user(session, "security-admin@example.com", "Security Admin")
            target = create_user(session, "security-target@example.com", "Security Target")
            admin.is_admin = True
            admin.security_level = 5
            session.add(admin)
            session.commit()

            updated = update_admin_user(session, target.id, AdminUserUpdate(security_level=3))
            users = list_admin_users(session)
            audit_log = session.query(AuditLog).filter(AuditLog.action == "admin.update_user").one()

            self.assertEqual(updated.security_level, 3)
            self.assertIn(target.id, {user.id for user in users})
            self.assertEqual(audit_log.resource_id, target.id)
            self.assertEqual(audit_log.security_level, 3)

    def test_admin_update_keeps_at_least_one_active_admin(self) -> None:
        with isolated_session() as session:
            admin = create_user(session, "last-admin@example.com", "Last Admin")
            admin.is_admin = True
            admin.security_level = 5
            session.add(admin)
            session.commit()

            with self.assertRaises(HTTPException) as deactivate_error:
                update_admin_user(
                    session,
                    admin.id,
                    AdminUserUpdate(is_active=False),
                    actor_user_id=admin.id,
                )
            self.assertEqual(deactivate_error.exception.status_code, 400)

            with self.assertRaises(HTTPException) as demote_error:
                update_admin_user(
                    session,
                    admin.id,
                    AdminUserUpdate(is_admin=False),
                    actor_user_id=admin.id,
                )
            self.assertEqual(demote_error.exception.status_code, 400)

            session.refresh(admin)
            self.assertTrue(admin.is_active)
            self.assertTrue(admin.is_admin)
            denied_logs = session.query(AuditLog).filter(
                AuditLog.action == "admin.update_user",
                AuditLog.outcome == "denied",
            ).all()
            self.assertEqual(len(denied_logs), 2)

            backup_admin = create_user(session, "backup-admin@example.com", "Backup Admin")
            backup_admin.is_admin = True
            backup_admin.security_level = 5
            session.add(backup_admin)
            session.commit()

            updated = update_admin_user(
                session,
                admin.id,
                AdminUserUpdate(is_admin=False),
                actor_user_id=backup_admin.id,
            )
            self.assertFalse(updated.is_admin)

    def test_rag_answer_records_audit_event(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "audit-rag@example.com", "Audit RAG")
            user.security_level = 2
            kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="Audit RAG KB", visibility="private"))
            chunk = RetrievedChunk(
                chunk_id="chunk-1",
                document_id="doc-1",
                knowledge_base_id=kb.id,
                chunk_index=0,
                content="Public policy content.",
                score=0.9,
                file_name="policy.md",
                title_path=None,
                page_number=None,
                section_name=None,
                metadata={},
                security_level=2,
            )
            retrieval = SimpleNamespace(
                question="policy",
                rewritten_query="policy",
                sub_questions=[],
                expanded_queries=["policy"],
                retrieval_routes=["dense"],
                candidates=[{"chunk_id": "chunk-1"}],
                selected_chunks=[chunk],
                selected_chunk_logs=[{"chunk_id": "chunk-1", "security_level": 2}],
                rrf_k=60,
                compression_chars_saved=0,
            )

            with (
                patch("app.services.qa_service.retrieve_advanced_chunks", return_value=retrieval),
                patch("app.rag.answering.get_llm_provider", return_value=FakeRagLlmProvider()),
            ):
                answer = build_rag_answer(session, user.id, kb.id, "policy")

            audit_log = session.query(AuditLog).filter(AuditLog.action == "rag.retrieve").one()
            self.assertEqual(answer.retrieval_log_id, audit_log.extra_metadata["retrieval_log_id"])
            self.assertEqual(audit_log.security_level, 5)
            self.assertEqual(audit_log.extra_metadata["selected_count"], 1)

    def test_private_knowledge_base_rag_uses_full_security_range_for_authorized_user(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "private-rag@example.com", "Private RAG")
            user.security_level = 1
            session.add(user)
            session.commit()
            kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="Private RAG KB", visibility="private"))
            chunk = RetrievedChunk(
                chunk_id="chunk-private",
                document_id="doc-private",
                knowledge_base_id=kb.id,
                chunk_index=0,
                content="Private high security policy content.",
                score=0.9,
                file_name="private.md",
                title_path=None,
                page_number=None,
                section_name=None,
                metadata={},
                security_level=5,
            )
            retrieval = SimpleNamespace(
                question="policy",
                rewritten_query="policy",
                sub_questions=[],
                expanded_queries=["policy"],
                retrieval_routes=["dense"],
                candidates=[{"chunk_id": "chunk-private"}],
                selected_chunks=[chunk],
                selected_chunk_logs=[{"chunk_id": "chunk-private", "security_level": 5}],
                rrf_k=60,
                compression_chars_saved=0,
            )

            with (
                patch("app.services.qa_service.retrieve_advanced_chunks", return_value=retrieval) as retrieve,
                patch("app.rag.answering.get_llm_provider", return_value=FakeRagLlmProvider()),
            ):
                build_rag_answer(session, user.id, kb.id, "policy")

            self.assertEqual(retrieve.call_args.kwargs["max_security_level"], 5)


def make_document(kb_id: str, file_name: str, content_hash: str, security_level: int) -> Document:
    return Document(
        knowledge_base_id=kb_id,
        file_name=file_name,
        file_ext="md",
        file_size=128,
        object_key=f"objects/{file_name}",
        content_hash=content_hash,
        status="indexed",
        security_level=security_level,
    )


def make_chunk(document: Document, content: str, security_level: int) -> DocumentChunk:
    return DocumentChunk(
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        chunk_index=0,
        content=content,
        token_count=len(content.split()),
        qdrant_point_id=f"point-{document.id}",
        security_level=security_level,
    )


if __name__ == "__main__":
    unittest.main()

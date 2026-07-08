from __future__ import annotations

import unittest
from unittest.mock import patch

from app.db.models.document import Document, DocumentChunk
from app.rag.advanced_retrieval import (
    RetrievalCandidate,
    fuse_candidates,
    hydrate_retrieved_chunks,
    plan_retrieval_queries,
    retrieve_bm25_routes,
)
from app.rag.query_rewrite import QueryRewritePlan
from app.rag.retrieval import RetrievedChunk
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.knowledge_base_service import create_knowledge_base
from helpers import create_user, isolated_session


class AdvancedRetrievalTests(unittest.TestCase):
    def test_plan_uses_llm_rewrite_and_limited_sub_queries(self) -> None:
        rewrite_plan = QueryRewritePlan(
            rewritten_query="RAG retrieval and long-term memory answer flow",
            sub_questions=["RAG retrieval flow", "long-term memory answer flow"],
        )

        with patch("app.rag.advanced_retrieval.rewrite_query", return_value=rewrite_plan):
            rewritten, sub_queries, retrieval_queries = plan_retrieval_queries(
                "How do RAG and long-term memory participate in answering?"
            )

        self.assertEqual(rewritten, "RAG retrieval and long-term memory answer flow")
        self.assertEqual(sub_queries, ["RAG retrieval flow", "long-term memory answer flow"])
        self.assertEqual(
            retrieval_queries,
            [
                "How do RAG and long-term memory participate in answering?",
                "RAG retrieval and long-term memory answer flow",
                "RAG retrieval flow",
                "long-term memory answer flow",
            ],
        )

    def test_plan_falls_back_when_llm_rewrite_is_unavailable(self) -> None:
        rewrite_plan = QueryRewritePlan(rewritten_query="RAG fallback behavior", sub_questions=[])

        with patch("app.rag.advanced_retrieval.rewrite_query", return_value=rewrite_plan):
            rewritten, sub_queries, retrieval_queries = plan_retrieval_queries("Please explain RAG?")

        self.assertEqual(rewritten, "RAG fallback behavior")
        self.assertEqual(sub_queries, [])
        self.assertEqual(retrieval_queries, ["Please explain RAG?", "RAG fallback behavior"])

    def test_rrf_dedupes_chunks_and_rewards_multi_route_hits(self) -> None:
        chunk_a = make_chunk("a", score=0.3)
        chunk_b = make_chunk("b", score=0.9)

        fused = fuse_candidates(
            {
                "dense_original": [
                    make_candidate(chunk_b, "dense_original", rank=1, score=0.9, weight=1.2),
                    make_candidate(chunk_a, "dense_original", rank=2, score=0.3, weight=1.2),
                ],
                "bm25_subquery_1": [
                    make_candidate(chunk_a, "bm25_subquery_1", rank=1, score=2.0, weight=1.0),
                ],
            },
            rrf_k=60,
        )

        self.assertEqual([item.chunk.chunk_id for item in fused], ["a", "b"])
        self.assertEqual(fused[0].routes, ["dense_original", "bm25_subquery_1"])
        self.assertGreater(fused[0].rrf_score, fused[1].rrf_score)

    def test_dense_hits_are_hydrated_from_indexed_database_chunks(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "dense-hydrate@example.com", "Dense Hydrate")
            kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="Hydrate KB"))
            indexed_document = make_document_row(kb.id, "indexed.md", "indexed-hash", status="indexed")
            failed_document = make_document_row(kb.id, "failed.md", "failed-hash", status="failed")
            session.add_all([indexed_document, failed_document])
            session.flush()
            indexed_chunk = make_chunk_row(indexed_document, "authoritative policy content")
            failed_chunk = make_chunk_row(failed_document, "stale failed content")
            session.add_all([indexed_chunk, failed_chunk])
            session.commit()

            hydrated = hydrate_retrieved_chunks(
                session,
                [
                    make_chunk(indexed_chunk.id, score=0.9, content="stale qdrant payload"),
                    make_chunk(failed_chunk.id, score=0.8),
                    make_chunk("missing-chunk", score=0.7),
                ],
                kb.id,
                max_security_level=1,
            )

            self.assertEqual([chunk.chunk_id for chunk in hydrated], [indexed_chunk.id])
            self.assertEqual(hydrated[0].content, "authoritative policy content")
            self.assertEqual(hydrated[0].file_name, "indexed.md")

    def test_bm25_ignores_chunks_from_non_indexed_documents(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "bm25-indexed@example.com", "BM25 Indexed")
            kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="BM25 KB"))
            indexed_document = make_document_row(kb.id, "indexed.md", "indexed-bm25", status="indexed")
            failed_document = make_document_row(kb.id, "failed.md", "failed-bm25", status="failed")
            session.add_all([indexed_document, failed_document])
            session.flush()
            session.add_all(
                [
                    make_chunk_row(indexed_document, "expense policy allows hotel reimbursement"),
                    make_chunk_row(failed_document, "expense policy stale executive reimbursement"),
                ]
            )
            session.commit()

            routes = retrieve_bm25_routes(
                session,
                kb.id,
                ["expense policy reimbursement"],
                route_limit=10,
                max_security_level=1,
            )

            self.assertEqual({item.chunk.file_name for item in routes["bm25_original"]}, {"indexed.md"})

    def test_bm25_can_search_multiple_authorized_knowledge_bases(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "bm25-multi@example.com", "BM25 Multi")
            first_kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="First BM25 KB"))
            second_kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="Second BM25 KB"))
            other_kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="Other BM25 KB"))
            first_document = make_document_row(first_kb.id, "first.md", "first-bm25-multi", status="indexed")
            second_document = make_document_row(second_kb.id, "second.md", "second-bm25-multi", status="indexed")
            other_document = make_document_row(other_kb.id, "other.md", "other-bm25-multi", status="indexed")
            session.add_all([first_document, second_document, other_document])
            session.flush()
            session.add_all(
                [
                    make_chunk_row(first_document, "expense policy for travel"),
                    make_chunk_row(second_document, "expense policy for meals"),
                    make_chunk_row(other_document, "expense policy outside scope"),
                ]
            )
            session.commit()

            routes = retrieve_bm25_routes(
                session,
                [first_kb.id, second_kb.id],
                ["expense policy"],
                route_limit=10,
                max_security_level=1,
            )

            self.assertEqual({item.chunk.file_name for item in routes["bm25_original"]}, {"first.md", "second.md"})

    def test_bm25_can_match_file_and_section_metadata(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "bm25-metadata@example.com", "BM25 Metadata")
            kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="Metadata BM25 KB"))
            document = make_document_row(kb.id, "travel-policy.md", "metadata-bm25", status="indexed")
            session.add(document)
            session.flush()
            chunk = make_chunk_row(document, "hotel reimbursement limit is 300")
            chunk.title_path = "Finance / Travel"
            chunk.section_name = "Travel Rules"
            session.add(chunk)
            session.commit()

            routes = retrieve_bm25_routes(
                session,
                kb.id,
                ["travel rules"],
                route_limit=10,
                max_security_level=1,
            )

            self.assertEqual([item.chunk.file_name for item in routes["bm25_original"]], ["travel-policy.md"])

def make_chunk(chunk_id: str, score: float, content: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        knowledge_base_id="kb",
        chunk_index=0,
        content=content or f"content {chunk_id}",
        score=score,
        file_name=f"{chunk_id}.md",
        title_path=None,
        page_number=None,
        section_name=None,
        metadata={},
    )


def make_candidate(
    chunk: RetrievedChunk,
    route: str,
    rank: int,
    score: float,
    weight: float,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk=chunk,
        route=route,
        query="query",
        query_index=0,
        rank=rank,
        score=score,
        weight=weight,
        matched_terms=[],
    )


def make_document_row(kb_id: str, file_name: str, content_hash: str, status: str) -> Document:
    return Document(
        knowledge_base_id=kb_id,
        file_name=file_name,
        file_ext="md",
        file_size=64,
        object_key=f"objects/{file_name}",
        content_hash=content_hash,
        status=status,
    )


def make_chunk_row(document: Document, content: str) -> DocumentChunk:
    return DocumentChunk(
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        chunk_index=0,
        content=content,
        token_count=max(1, len(content.split())),
        security_level=1,
    )


if __name__ == "__main__":
    unittest.main()

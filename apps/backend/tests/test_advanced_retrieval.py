from __future__ import annotations

import unittest
from unittest.mock import patch

from app.db.models.document import Document, DocumentChunk
from app.rag.advanced_retrieval import (
    FusedCandidate,
    RetrievalCandidate,
    bm25_document_terms,
    bm25_query_terms,
    fuse_candidates,
    hydrate_retrieved_chunks,
    retrieve_advanced_chunks,
    retrieve_bm25_route,
)
from app.rag.retrieval import RetrievedChunk
from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.knowledge_base_service import create_knowledge_base
from helpers import create_user, isolated_session


class AdvancedRetrievalTests(unittest.TestCase):
    def test_rrf_dedupes_chunks_and_rewards_multi_route_hits(self) -> None:
        chunk_a = make_chunk("a", score=0.3)
        chunk_b = make_chunk("b", score=0.9)

        fused = fuse_candidates(
            {
                "dense": [
                    make_candidate(chunk_b, "dense", rank=1, score=0.9),
                    make_candidate(chunk_a, "dense", rank=2, score=0.3),
                ],
                "bm25": [
                    make_candidate(chunk_a, "bm25", rank=1, score=2.0),
                ],
            },
            rrf_k=60,
        )

        self.assertEqual([item.chunk.chunk_id for item in fused], ["a", "b"])
        self.assertEqual(fused[0].routes, ["dense", "bm25"])
        self.assertGreater(fused[0].rrf_score, fused[1].rrf_score)

    def test_selected_chunks_preserve_full_content_without_keyword_compression(self) -> None:
        content = (
            "Sam Bankman-Fried was the former CEO of FTX. "
            "The prosecution accused him of committing fraud for personal gain."
        )
        chunk = make_chunk("full-content", score=0.9, content=content)
        fused = FusedCandidate(
            chunk=chunk,
            routes=["dense"],
            rrf_score=0.12345678,
            best_score=0.9,
            matched_terms=["fraud"],
        )
        with (
            patch("app.rag.advanced_retrieval.retrieve_dense_route", return_value={}),
            patch("app.rag.advanced_retrieval.retrieve_bm25_route", return_value={}),
            patch("app.rag.advanced_retrieval.fuse_candidates", return_value=[fused]),
        ):
            result = retrieve_advanced_chunks(None, "owner", "kb", "Who committed fraud?")

        self.assertEqual(result.selected_chunks[0].content, content)
        self.assertEqual(result.selected_chunks[0].rrf_score, 0.123457)
        self.assertEqual(result.selected_chunks[0].retrieval_routes, ["dense"])
        self.assertEqual(result.compression_chars_saved, 0)

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
                    make_chunk(indexed_chunk.id, score=0.9, content="stale dense payload"),
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

            routes = retrieve_bm25_route(
                session,
                kb.id,
                "expense policy reimbursement",
                route_limit=10,
                max_security_level=1,
            )

            self.assertEqual({item.chunk.file_name for item in routes["bm25"]}, {"indexed.md"})

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

            routes = retrieve_bm25_route(
                session,
                [first_kb.id, second_kb.id],
                "expense policy",
                route_limit=10,
                max_security_level=1,
            )

            self.assertEqual({item.chunk.file_name for item in routes["bm25"]}, {"first.md", "second.md"})

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

            routes = retrieve_bm25_route(
                session,
                kb.id,
                "travel rules",
                route_limit=10,
                max_security_level=1,
            )

            self.assertEqual([item.chunk.file_name for item in routes["bm25"]], ["travel-policy.md"])

    def test_bm25_uses_terms_beyond_the_matched_term_log_limit(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "bm25-full-document@example.com", "BM25 Full Document")
            kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="Full Document BM25 KB"))
            document = make_document_row(kb.id, "long-policy.md", "long-bm25", status="indexed")
            session.add(document)
            session.flush()
            prefix = " ".join(f"filler{index}" for index in range(40))
            session.add(make_chunk_row(document, f"{prefix} needleterm"))
            session.commit()

            routes = retrieve_bm25_route(
                session,
                kb.id,
                "needleterm",
                route_limit=10,
                max_security_level=1,
            )

            self.assertEqual([item.chunk.file_name for item in routes["bm25"]], ["long-policy.md"])

    def test_bm25_terms_remove_english_stop_words_and_preserve_document_frequency(self) -> None:
        self.assertEqual(bm25_query_terms("the fibrosis is in the lungs"), ["fibrosis", "lungs"])
        self.assertEqual(bm25_document_terms("fibrosis fibrosis in lungs"), ["fibrosis", "fibrosis", "lungs"])


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
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk=chunk,
        route=route,
        query="query",
        rank=rank,
        score=score,
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

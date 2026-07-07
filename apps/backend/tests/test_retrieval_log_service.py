from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.schemas.knowledge_base import KnowledgeBaseCreate
from app.services.knowledge_base_service import create_knowledge_base
from app.services.retrieval_log_service import create_retrieval_log, to_retrieval_log_read
from helpers import create_user, isolated_session


class RetrievalLogServiceTests(unittest.TestCase):
    def test_create_retrieval_log_defaults_reranker_disabled(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "retrieval-log@example.com", "Retrieval Log")
            kb = create_knowledge_base(session, user.id, KnowledgeBaseCreate(name="Retrieval Log KB"))
            result = SimpleNamespace(
                question="policy",
                scope_type="single",
                searched_knowledge_base_ids=[kb.id],
                rewritten_query="policy",
                sub_questions=[],
                expanded_queries=["policy"],
                retrieval_routes=["dense_original"],
                candidates=[],
                selected_chunk_logs=[],
                rrf_k=60,
                compression_chars_saved=0,
            )

            log = create_retrieval_log(session, user.id, kb.id, result)
            read = to_retrieval_log_read(log)

            self.assertFalse(log.reranker_enabled)
            self.assertFalse(read.reranker_enabled)


if __name__ == "__main__":
    unittest.main()

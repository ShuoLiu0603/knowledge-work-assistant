from __future__ import annotations

import unittest

from app.db.models.document import Document, DocumentChunk
from app.rag.vector_store import embedding_text


class VectorStoreTests(unittest.TestCase):
    def test_embedding_text_includes_document_and_section_metadata(self) -> None:
        document = Document(
            knowledge_base_id="kb-id",
            file_name="travel-policy.md",
            file_ext="md",
            file_size=128,
            object_key="objects/travel-policy.md",
            content_hash="hash",
            status="indexed",
        )
        chunk = DocumentChunk(
            document_id="doc-id",
            knowledge_base_id="kb-id",
            chunk_index=0,
            content="Hotel reimbursement limit is 300.",
            token_count=6,
            title_path="Finance / Travel",
            section_name="Travel Rules",
        )

        text = embedding_text(document, chunk)

        self.assertIn("file_name: travel-policy.md", text)
        self.assertIn("title_path: Finance / Travel", text)
        self.assertIn("section_name: Travel Rules", text)
        self.assertIn("content: Hotel reimbursement limit is 300.", text)


if __name__ == "__main__":
    unittest.main()

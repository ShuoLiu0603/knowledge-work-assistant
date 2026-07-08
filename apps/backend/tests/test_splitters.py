from __future__ import annotations

import unittest

from langchain_core.documents import Document

from app.rag.splitters import split_documents


class SplitterTests(unittest.TestCase):
    def test_split_documents_preserves_langchain_metadata(self) -> None:
        chunks = split_documents(
            [
                Document(
                    page_content="First page policy",
                    metadata={"page_number": 1, "section_name": "A", "title_path": "A"},
                ),
                Document(
                    page_content="Second page policy",
                    metadata={"page_number": 2, "section_name": "B", "title_path": "B"},
                ),
            ],
            chunk_size=100,
        )

        self.assertEqual([chunk.page_content for chunk in chunks], ["First page policy", "Second page policy"])
        self.assertEqual([chunk.metadata["page_number"] for chunk in chunks], [1, 2])
        self.assertEqual([chunk.metadata["section_name"] for chunk in chunks], ["A", "B"])

    def test_split_documents_adds_overlap_and_token_count(self) -> None:
        chunks = split_documents(
            [Document(page_content="abcdefghijklmnopqrstuvwxyz" * 3, metadata={})],
            chunk_size=20,
            chunk_overlap=5,
        )

        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].page_content[-5:], chunks[1].page_content[:5])
        self.assertGreater(chunks[0].metadata["token_count"], 0)


if __name__ == "__main__":
    unittest.main()

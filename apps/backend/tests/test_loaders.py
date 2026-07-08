from __future__ import annotations

import unittest
from io import BytesIO

from docx import Document as DocxDocument
from langchain_core.documents import Document

from app.rag.loaders import load_documents


class LoaderTests(unittest.TestCase):
    def test_load_txt_returns_langchain_documents(self) -> None:
        documents = load_documents("Alpha\n\nBeta".encode("utf-8"), "notes.txt", "txt")

        self.assertEqual(len(documents), 1)
        self.assertIsInstance(documents[0], Document)
        self.assertEqual(documents[0].page_content, "Alpha\n\nBeta")
        self.assertEqual(documents[0].metadata["file_name"], "notes.txt")

    def test_load_csv_returns_row_documents(self) -> None:
        documents = load_documents(
            "name,role\nAlice,Engineer\nBob,HR\n".encode("utf-8"),
            "people.csv",
            "csv",
        )

        self.assertEqual(len(documents), 2)
        self.assertEqual([document.metadata["block_type"] for document in documents], ["table", "table"])
        self.assertIn("name: Alice", documents[0].page_content)
        self.assertEqual(documents[0].metadata["row_number"], 1)

    def test_load_markdown_preserves_header_path(self) -> None:
        documents = load_documents(
            "# Policy\nIntro\n## Travel\nRules".encode("utf-8"),
            "policy.md",
            "md",
        )

        self.assertEqual([document.metadata["title_path"] for document in documents], ["Policy", "Policy / Travel"])
        self.assertEqual(documents[-1].metadata["section_name"], "Travel")

    def test_load_docx_uses_langchain_loader(self) -> None:
        document = DocxDocument()
        document.add_paragraph("Company policy")
        buffer = BytesIO()
        document.save(buffer)

        documents = load_documents(buffer.getvalue(), "policy.docx", "docx")

        self.assertEqual(len(documents), 1)
        self.assertIn("Company policy", documents[0].page_content)


if __name__ == "__main__":
    unittest.main()

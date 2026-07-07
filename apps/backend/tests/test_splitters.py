from __future__ import annotations

import unittest

from app.rag.loaders import ParsedBlock
from app.rag.splitters import split_blocks


class SplitterTests(unittest.TestCase):
    def test_split_blocks_flushes_when_page_or_section_changes(self) -> None:
        chunks = split_blocks(
            [
                ParsedBlock(text="First page policy", page_number=1, section_name="A", title_path="A"),
                ParsedBlock(text="Second page policy", page_number=2, section_name="A", title_path="A"),
                ParsedBlock(text="New section policy", page_number=2, section_name="B", title_path="B"),
            ],
            chunk_size=100,
        )

        self.assertEqual([chunk.content for chunk in chunks], ["First page policy", "Second page policy", "New section policy"])
        self.assertEqual([chunk.page_number for chunk in chunks], [1, 2, 2])
        self.assertEqual([chunk.section_name for chunk in chunks], ["A", "A", "B"])


if __name__ == "__main__":
    unittest.main()

"""Unit tests for DocumentParser Protocol."""

from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.document_parser import DocumentParser
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock


class _SatisfyingParser:
    async def parse(self, pdf_bytes: bytes, docling_config: DoclingConfig) -> ParsedDocument:
        _ = pdf_bytes
        _ = docling_config
        return ParsedDocument(
            blocks=(
                TextBlock(
                    text="x",
                    page_number=1,
                    bbox=BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
                    block_id="p1_b0",
                ),
            ),
            page_count=1,
        )


class _NonConformingParser:
    def something_else(self) -> None:
        pass


def test_satisfying_class_passes_runtime_check() -> None:
    parser = _SatisfyingParser()

    assert isinstance(parser, DocumentParser)


def test_non_conforming_class_fails_runtime_check() -> None:
    parser = _NonConformingParser()

    assert not isinstance(parser, DocumentParser)

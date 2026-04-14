"""Unit tests for ParsedDocument dataclass."""

import dataclasses

import pytest

from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock


def _block(page: int, block_id: str) -> TextBlock:
    return TextBlock(
        text=f"text {block_id}",
        page_number=page,
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=10.0, y1=10.0),
        block_id=block_id,
    )


def test_parsed_document_constructs_with_single_block() -> None:
    block = _block(1, "p1_b0")
    doc = ParsedDocument(blocks=(block,), page_count=1)

    assert isinstance(doc.blocks, tuple)
    assert doc.blocks == (block,)
    assert doc.page_count == 1


def test_parsed_document_blocks_field_is_tuple() -> None:
    doc = ParsedDocument(blocks=(_block(1, "a"), _block(1, "b")), page_count=1)

    assert isinstance(doc.blocks, tuple)


def test_parsed_document_is_frozen() -> None:
    doc = ParsedDocument(blocks=(_block(1, "a"),), page_count=1)

    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.blocks = ()  # type: ignore[misc]


def test_for_page_returns_blocks_in_original_order_for_matching_page() -> None:
    b1 = _block(1, "p1_b0")
    b2 = _block(2, "p2_b0")
    b3 = _block(1, "p1_b1")
    doc = ParsedDocument(blocks=(b1, b2, b3), page_count=2)

    result = doc.for_page(1)

    assert result == (b1, b3)


def test_for_page_returns_single_block_for_other_page() -> None:
    b1 = _block(1, "p1_b0")
    b2 = _block(2, "p2_b0")
    b3 = _block(1, "p1_b1")
    doc = ParsedDocument(blocks=(b1, b2, b3), page_count=2)

    assert doc.for_page(2) == (b2,)


def test_for_page_returns_empty_tuple_for_missing_page() -> None:
    doc = ParsedDocument(blocks=(_block(1, "p1_b0"),), page_count=1)

    result = doc.for_page(99)

    assert result == ()
    assert isinstance(result, tuple)


def test_for_page_returns_tuple_type() -> None:
    doc = ParsedDocument(blocks=(_block(1, "p1_b0"),), page_count=1)

    assert isinstance(doc.for_page(1), tuple)

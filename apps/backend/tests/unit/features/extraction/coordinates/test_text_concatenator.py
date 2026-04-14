"""Unit tests for TextConcatenator — single-pass join + index build."""

import bisect

import pytest

from app.features.extraction.coordinates import offset_index as offset_index_module
from app.features.extraction.coordinates.text_concatenator import TextConcatenator
from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock

_BBOX = BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)


def _block(text: str, page: int, block_id: str) -> TextBlock:
    return TextBlock(text=text, page_number=page, bbox=_BBOX, block_id=block_id)


def _doc(*blocks: TextBlock, page_count: int | None = None) -> ParsedDocument:
    pages = (
        page_count if page_count is not None else max((b.page_number for b in blocks), default=0)
    )
    return ParsedDocument(blocks=tuple(blocks), page_count=pages)


def test_three_blocks_default_separator_joins_with_double_newline() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    text, index = TextConcatenator().concatenate(doc)

    assert text == "hello\n\nworld\n\nfoo"
    assert index.block_count() == 3


def test_three_blocks_lookup_first_char_of_first_block() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(0) == ("p1_b0", 0)


def test_three_blocks_lookup_last_char_of_first_block() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(4) == ("p1_b0", 4)


def test_three_blocks_lookup_inside_separator_returns_none() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(5) is None
    assert index.lookup(6) is None


def test_three_blocks_lookup_start_of_second_block() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(7) == ("p1_b1", 0)


def test_three_blocks_lookup_last_char_of_second_block() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(11) == ("p1_b1", 4)


def test_three_blocks_lookup_first_char_of_third_block() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(14) == ("p2_b0", 0)


def test_three_blocks_lookup_last_char_of_third_block() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(16) == ("p2_b0", 2)


def test_three_blocks_lookup_total_length_returns_none() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(17) is None


def test_three_blocks_lookup_far_past_end_returns_none() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(100) is None


def test_three_blocks_lookup_negative_returns_none() -> None:
    doc = _doc(
        _block("hello", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
        _block("foo", 2, "p2_b0"),
    )

    _, index = TextConcatenator().concatenate(doc)

    assert index.lookup(-1) is None


def test_empty_document_returns_empty_string_and_empty_index() -> None:
    doc = ParsedDocument(blocks=(), page_count=0)

    text, index = TextConcatenator().concatenate(doc)

    assert text == ""
    assert index.block_count() == 0
    assert index.lookup(0) is None


def test_single_block_no_separator_appended() -> None:
    doc = _doc(_block("only", 1, "p1_b0"))

    text, index = TextConcatenator().concatenate(doc)

    assert text == "only"
    assert index.block_count() == 1
    assert index.lookup(0) == ("p1_b0", 0)
    assert index.lookup(3) == ("p1_b0", 3)
    assert index.lookup(4) is None


def test_empty_text_block_collapses_to_zero_width_entry() -> None:
    doc = _doc(
        _block("", 1, "p1_b0"),
        _block("world", 1, "p1_b1"),
    )

    text, index = TextConcatenator().concatenate(doc)

    assert text == "\n\nworld"
    assert index.block_count() == 2
    assert index.lookup(0) is None
    assert index.lookup(2) == ("p1_b1", 0)
    assert index.lookup(6) == ("p1_b1", 4)


def test_custom_separator_is_used() -> None:
    doc = _doc(
        _block("a", 1, "p1_b0"),
        _block("b", 1, "p1_b1"),
    )

    text, index = TextConcatenator(separator=" | ").concatenate(doc)

    assert text == "a | b"
    assert index.lookup(0) == ("p1_b0", 0)
    assert index.lookup(1) is None
    assert index.lookup(2) is None
    assert index.lookup(3) is None
    assert index.lookup(4) == ("p1_b1", 0)


def test_input_document_is_not_mutated() -> None:
    block_a = _block("hello", 1, "p1_b0")
    block_b = _block("world", 1, "p1_b1")
    doc = _doc(block_a, block_b)

    pre_blocks = doc.blocks
    pre_texts = [b.text for b in doc.blocks]
    pre_ids = [b.block_id for b in doc.blocks]

    TextConcatenator().concatenate(doc)

    assert doc.blocks is pre_blocks
    assert [b.text for b in doc.blocks] == pre_texts
    assert [b.block_id for b in doc.blocks] == pre_ids


def test_1000_blocks_lookup_uses_bisect_exactly_once_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deterministic replacement for the spec's "<100 ms for 100 random
    # lookups" perf sanity. A linear scan would call bisect.bisect_right
    # zero times; binary search calls it exactly once per lookup. We verify
    # the latter on a 1000-block document with 100 deterministic offsets.
    blocks = tuple(_block(f"text_{i}", page=1, block_id=f"p1_b{i}") for i in range(1000))
    doc = ParsedDocument(blocks=blocks, page_count=1)

    text, index = TextConcatenator().concatenate(doc)
    total_len = len(text)

    call_count = 0
    real_bisect_right = bisect.bisect_right

    def counting_bisect_right(
        a: object,
        x: object,
        lo: int = 0,
        hi: int | None = None,
    ) -> int:
        nonlocal call_count
        call_count += 1
        if hi is None:
            return real_bisect_right(a, x)  # type: ignore[call-overload]
        return real_bisect_right(a, x, lo, hi)  # type: ignore[call-overload]

    monkeypatch.setattr(offset_index_module.bisect, "bisect_right", counting_bisect_right)

    offsets = [(i * 37) % total_len for i in range(100)]
    for offset in offsets:
        index.lookup(offset)

    assert call_count == 100

"""Unit tests for OffsetIndexEntry — immutable (start, end, block_id) record."""

import dataclasses

import pytest

from app.features.extraction.coordinates.offset_index_entry import OffsetIndexEntry


def test_entry_holds_start_end_block_id() -> None:
    entry = OffsetIndexEntry(start=0, end=5, block_id="p1_b0")

    assert entry.start == 0
    assert entry.end == 5
    assert entry.block_id == "p1_b0"


def test_entry_is_frozen() -> None:
    entry = OffsetIndexEntry(start=0, end=5, block_id="p1_b0")

    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.start = 99  # type: ignore[misc]  # asserting frozen behavior at runtime


def test_entry_allows_zero_width_range() -> None:
    # Empty-text blocks legitimately produce start == end entries; these must
    # be constructible so TextConcatenator's zero-width case keeps working.
    entry = OffsetIndexEntry(start=7, end=7, block_id="empty_block")

    assert entry.start == entry.end == 7


def test_entry_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="start <= end"):
        OffsetIndexEntry(start=10, end=5, block_id="bad")


def test_entry_rejects_negative_start() -> None:
    with pytest.raises(ValueError, match="start must be non-negative"):
        OffsetIndexEntry(start=-1, end=5, block_id="bad")


def test_entry_rejects_negative_end() -> None:
    with pytest.raises(ValueError, match="end must be non-negative"):
        OffsetIndexEntry(start=0, end=-1, block_id="bad")

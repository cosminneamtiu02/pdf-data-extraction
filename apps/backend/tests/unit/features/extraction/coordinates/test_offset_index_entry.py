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

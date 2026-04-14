"""Unit tests for OffsetIndex — O(log n) char-offset-to-block lookup."""

import bisect

import pytest

from app.features.extraction.coordinates import offset_index as offset_index_module
from app.features.extraction.coordinates.offset_index import OffsetIndex
from app.features.extraction.coordinates.offset_index_entry import OffsetIndexEntry


def _three_block_index() -> OffsetIndex:
    # Mirrors "hello\n\nworld\n\nfoo" — blocks of length 5, 5, 3 with 2-char gaps.
    return OffsetIndex(
        entries=[
            OffsetIndexEntry(start=0, end=5, block_id="p1_b0"),
            OffsetIndexEntry(start=7, end=12, block_id="p1_b1"),
            OffsetIndexEntry(start=14, end=17, block_id="p2_b0"),
        ],
    )


def test_block_count_reflects_entry_count() -> None:
    index = _three_block_index()

    assert index.block_count() == 3


def test_lookup_at_start_of_first_block() -> None:
    index = _three_block_index()

    assert index.lookup(0) == ("p1_b0", 0)


def test_lookup_at_last_char_of_first_block() -> None:
    index = _three_block_index()

    assert index.lookup(4) == ("p1_b0", 4)


def test_lookup_inside_separator_returns_none() -> None:
    index = _three_block_index()

    assert index.lookup(5) is None
    assert index.lookup(6) is None


def test_lookup_at_start_of_second_block() -> None:
    index = _three_block_index()

    assert index.lookup(7) == ("p1_b1", 0)


def test_lookup_at_last_char_of_second_block() -> None:
    index = _three_block_index()

    assert index.lookup(11) == ("p1_b1", 4)


def test_lookup_at_start_of_third_block() -> None:
    index = _three_block_index()

    assert index.lookup(14) == ("p2_b0", 0)


def test_lookup_at_last_char_of_third_block() -> None:
    index = _three_block_index()

    assert index.lookup(16) == ("p2_b0", 2)


def test_lookup_at_total_length_is_none() -> None:
    index = _three_block_index()

    assert index.lookup(17) is None


def test_lookup_far_past_end_is_none() -> None:
    index = _three_block_index()

    assert index.lookup(100) is None


def test_lookup_negative_offset_is_none() -> None:
    index = _three_block_index()

    assert index.lookup(-1) is None


def test_empty_index_block_count_is_zero() -> None:
    index = OffsetIndex(entries=[])

    assert index.block_count() == 0


def test_empty_index_lookup_returns_none() -> None:
    index = OffsetIndex(entries=[])

    assert index.lookup(0) is None


def test_lookup_zero_width_entry_is_never_returned() -> None:
    # An empty-text block produces a zero-width entry (start == end). No
    # character in the concatenated text actually belongs to it, so lookup
    # must skip it even when the offset equals its start.
    index = OffsetIndex(
        entries=[
            OffsetIndexEntry(start=0, end=0, block_id="empty_block"),
            OffsetIndexEntry(start=2, end=7, block_id="world_block"),
        ],
    )

    assert index.lookup(0) is None
    assert index.lookup(2) == ("world_block", 0)


def test_lookup_uses_bisect_exactly_once_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deterministic replacement for the wall-clock "sublinear" check: spy on
    # bisect.bisect_right and assert each lookup triggers exactly one call.
    # A linear scan would either call it zero times (walking entries directly)
    # or more than once (rescanning). Exactly-one is the binary-search contract.
    index = OffsetIndex(
        entries=[
            OffsetIndexEntry(start=i * 10, end=i * 10 + 5, block_id=f"b{i}") for i in range(10_000)
        ],
    )

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

    for target in (0, 500, 50_000, 99_995, 100_000):
        index.lookup(target)

    assert call_count == 5


def test_lookup_rejects_unordered_entries() -> None:
    with pytest.raises(ValueError, match="ordered and non-overlapping"):
        OffsetIndex(
            entries=[
                OffsetIndexEntry(start=10, end=15, block_id="b0"),
                OffsetIndexEntry(start=5, end=8, block_id="b1"),
            ],
        )


def test_lookup_rejects_overlapping_entries() -> None:
    with pytest.raises(ValueError, match="ordered and non-overlapping"):
        OffsetIndex(
            entries=[
                OffsetIndexEntry(start=0, end=10, block_id="b0"),
                OffsetIndexEntry(start=5, end=15, block_id="b1"),
            ],
        )


def test_lookup_accepts_adjacent_entries_with_no_gap() -> None:
    # Back-to-back entries (previous end == next start) are legal: the half-open
    # interval convention means the boundary offset belongs to the next block.
    index = OffsetIndex(
        entries=[
            OffsetIndexEntry(start=0, end=5, block_id="b0"),
            OffsetIndexEntry(start=5, end=10, block_id="b1"),
        ],
    )

    assert index.lookup(4) == ("b0", 4)
    assert index.lookup(5) == ("b1", 0)

"""Unit tests for OffsetIndex — O(log n) char-offset-to-block lookup."""

import time

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


def test_lookup_is_sublinear_on_large_index() -> None:
    # Timing ratio sanity check: a 10_000-entry index must not be linearly
    # slower than a 100-entry index. bisect guarantees the ratio stays near 1.
    small = OffsetIndex(
        entries=[
            OffsetIndexEntry(start=i * 10, end=i * 10 + 5, block_id=f"b{i}") for i in range(100)
        ],
    )
    large = OffsetIndex(
        entries=[
            OffsetIndexEntry(start=i * 10, end=i * 10 + 5, block_id=f"b{i}") for i in range(10_000)
        ],
    )

    iterations = 2_000

    def measure(index: OffsetIndex, target: int) -> float:
        t0 = time.perf_counter()
        for _ in range(iterations):
            index.lookup(target)
        return time.perf_counter() - t0

    small_time = measure(small, 500)
    large_time = measure(large, 50_000)

    # Linear scan would put large at ~100x small. bisect keeps it near-constant.
    # A generous 20x bound still rejects any accidental linear scan.
    assert large_time < small_time * 20

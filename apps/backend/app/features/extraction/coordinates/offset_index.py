"""OffsetIndex: O(log n) lookup from a character offset back to its source block."""

import bisect
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.features.extraction.coordinates.offset_index_entry import OffsetIndexEntry


@dataclass(frozen=True)
class OffsetIndex:
    """Ordered index mapping a character offset in a concatenated text to the
    block that produced it.

    Each entry is a half-open range `[start, end)` in the concatenated text.
    Consecutive entries are strictly ordered by `start` and never overlap, but
    may have gaps (the separator characters between blocks fall into these
    gaps and produce a `None` result on lookup). These invariants are
    validated in `__post_init__` so a mistakenly constructed index fails
    loudly at build time rather than silently returning wrong lookups.

    The internal `_starts` tuple is a parallel array of entry starts
    maintained for `bisect_right`, which is the O(log n) hot path used by
    `SpanResolver` to translate LangExtract offsets back to blocks once per
    extracted field. It is stored as a tuple so the frozen-dataclass
    immutability guarantee extends to the derived array, not just the entry
    collection.
    """

    entries: Sequence[OffsetIndexEntry]
    _starts: tuple[int, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # `entries` may arrive as any Sequence; snapshot to a tuple so the
        # frozen guarantee extends to the collection itself.
        entries = tuple(self.entries)
        object.__setattr__(self, "entries", entries)

        previous_end: int | None = None
        for idx, entry in enumerate(entries):
            # Per-entry start <= end is already enforced by OffsetIndexEntry
            # itself; here we only need cross-entry ordering + non-overlap.
            if previous_end is not None and entry.start < previous_end:
                msg = (
                    f"OffsetIndex entries must be ordered and non-overlapping: "
                    f"entry at index {idx} has start={entry.start} which is "
                    f"less than previous entry's end={previous_end}"
                )
                raise ValueError(msg)
            previous_end = entry.end

        object.__setattr__(self, "_starts", tuple(entry.start for entry in entries))

    def lookup(self, char_offset: int) -> tuple[str, int] | None:
        """Return `(block_id, offset_within_block)` for the block containing
        `char_offset`, or `None` if the offset falls in a separator gap or
        outside every block (including negative offsets).
        """
        if char_offset < 0 or not self.entries:
            return None

        # bisect_right returns the insertion point to the RIGHT of any equal
        # elements. Subtracting 1 yields the index of the greatest entry whose
        # start is <= char_offset — the only candidate that could contain it.
        idx = bisect.bisect_right(self._starts, char_offset) - 1
        if idx < 0:
            return None

        entry = self.entries[idx]
        if char_offset >= entry.end:
            # Candidate exists but the offset is past its exclusive end, so
            # char_offset lands in the separator between this block and the
            # next (or past the final block). Either way: not in any block.
            return None

        return entry.block_id, char_offset - entry.start

    def block_count(self) -> int:
        """Number of indexed blocks. Debug/diagnostic helper."""
        return len(self.entries)

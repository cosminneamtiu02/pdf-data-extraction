"""OffsetIndexEntry: immutable (start, end, block_id) record for the offset index."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OffsetIndexEntry:
    """One entry in an `OffsetIndex`: the half-open range `[start, end)` in the
    concatenated text that came from block `block_id`.

    `end` is exclusive — the block occupies offsets `start` through `end - 1`
    inclusive. `start == end` is allowed for blocks whose text is empty; such
    entries contain no character and `OffsetIndex.lookup` will never return
    them.
    """

    start: int
    end: int
    block_id: str

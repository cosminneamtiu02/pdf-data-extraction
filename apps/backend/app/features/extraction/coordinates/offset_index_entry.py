"""OffsetIndexEntry: immutable (start, end, block_id) record for the offset index."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OffsetIndexEntry:
    """One entry in an `OffsetIndex`: the half-open range `[start, end)` in the
    concatenated text that came from block `block_id`.

    `end` is exclusive — the block occupies offsets `start` through `end - 1`
    inclusive. `start == end` is allowed for blocks whose text is empty; such
    entries contain no character and `OffsetIndex.lookup` will never return
    them. `start > end` and negative offsets are rejected at construction so
    malformed entries cannot silently corrupt `OffsetIndex` lookups.
    """

    start: int
    end: int
    block_id: str

    def __post_init__(self) -> None:
        if self.start < 0:
            msg = f"OffsetIndexEntry.start must be non-negative, got {self.start}"
            raise ValueError(msg)
        if self.end < 0:
            msg = f"OffsetIndexEntry.end must be non-negative, got {self.end}"
            raise ValueError(msg)
        if self.start > self.end:
            msg = f"OffsetIndexEntry requires start <= end, got start={self.start}, end={self.end}"
            raise ValueError(msg)

"""Immutable half-open character range inside a block's text."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CharRange:
    """Half-open `[start, end)` character range, indices into a block's text.

    `start == end` is allowed (vacuous / empty range). `start > end` is a bug
    and raises at construction.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start > self.end:
            msg = f"CharRange requires start <= end, got start={self.start}, end={self.end}"
            raise ValueError(msg)

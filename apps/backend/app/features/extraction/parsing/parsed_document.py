"""ParsedDocument: immutable container of TextBlocks with page count."""

from dataclasses import dataclass

from app.features.extraction.parsing.text_block import TextBlock


@dataclass(frozen=True)
class ParsedDocument:
    """The full result of parsing a PDF: an ordered tuple of TextBlocks and a page count.

    blocks is a tuple (not a list) so the frozen semantics extend to the collection
    itself, not just the field reference.
    """

    blocks: tuple[TextBlock, ...]
    page_count: int

    def __post_init__(self) -> None:
        # The isinstance is redundant under the static type, but defending the
        # runtime invariant against untyped callers is the whole point.
        if not isinstance(self.blocks, tuple):  # pyright: ignore[reportUnnecessaryIsInstance]
            object.__setattr__(self, "blocks", tuple(self.blocks))

    def for_page(self, page: int) -> tuple[TextBlock, ...]:
        """Return all blocks on the given 1-indexed page in their original order."""
        return tuple(block for block in self.blocks if block.page_number == page)

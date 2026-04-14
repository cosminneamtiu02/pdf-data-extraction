"""TextBlock: immutable parser-emitted text fragment with bounding box."""

from dataclasses import dataclass

from app.features.extraction.parsing.bounding_box import BoundingBox


@dataclass(frozen=True)
class TextBlock:
    """A single text block emitted by a DocumentParser.

    page_number is 1-indexed (page 1 is the first page) to match how humans refer
    to pages and PyMuPDF's `doc[page_number - 1]` ergonomics.
    block_id is a stable identifier within a single parse result, used by the
    coordinate matching layer to key offset-to-block mappings.
    """

    text: str
    page_number: int
    bbox: BoundingBox
    block_id: str

    def __post_init__(self) -> None:
        if self.page_number < 1:
            msg = f"TextBlock.page_number must be 1-indexed, got {self.page_number}"
            raise ValueError(msg)

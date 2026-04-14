"""TextConcatenator: single-pass join of ParsedDocument blocks + OffsetIndex build."""

from app.features.extraction.coordinates.offset_index import OffsetIndex
from app.features.extraction.coordinates.offset_index_entry import OffsetIndexEntry
from app.features.extraction.parsing.parsed_document import ParsedDocument

_DEFAULT_SEPARATOR = "\n\n"


class TextConcatenator:
    """Join a `ParsedDocument`'s blocks into a single string and build an
    `OffsetIndex` simultaneously in one pass.

    LangExtract operates on a concatenated text blob and returns character
    offsets into it; the `OffsetIndex` is the bridge that lets us resolve those
    offsets back to the Docling-emitted `TextBlock` they came from.

    The separator (default `"\\n\\n"`) is fixed at the global level for v1;
    the constructor parameter exists so unit tests can pin it to something
    visually distinct like `" | "`.
    """

    def __init__(self, separator: str = _DEFAULT_SEPARATOR) -> None:
        self._separator = separator

    def concatenate(self, document: ParsedDocument) -> tuple[str, OffsetIndex]:
        """Return `(concatenated_text, offset_index)` for `document`.

        The input document is never mutated. Empty block lists return
        `("", OffsetIndex(entries=[]))`. Empty block text produces a
        zero-width `OffsetIndexEntry` (`start == end`) that `lookup` will
        never return, which is the correct behavior: no character in the
        concatenated text actually belongs to an empty block.
        """
        if not document.blocks:
            return "", OffsetIndex(entries=[])

        parts: list[str] = []
        entries: list[OffsetIndexEntry] = []
        cursor = 0
        separator_length = len(self._separator)

        for i, block in enumerate(document.blocks):
            if i > 0:
                parts.append(self._separator)
                cursor += separator_length

            start = cursor
            parts.append(block.text)
            cursor += len(block.text)
            entries.append(
                OffsetIndexEntry(start=start, end=cursor, block_id=block.block_id),
            )

        return "".join(parts), OffsetIndex(entries=entries)

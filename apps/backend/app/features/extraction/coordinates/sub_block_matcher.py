"""Three-step fallback matcher for locating values inside a block's text."""

import unicodedata

from app.features.extraction.coordinates.char_range import CharRange


class SubBlockMatcher:
    """Locate a value inside a block's text via a three-step fallback chain.

    The chain is:

    1. Direct substring via `str.find`.
    2. Whitespace-collapsed substring: runs of whitespace in both strings are
       collapsed to a single space, then searched; success is translated back
       to original `block_text` indices via a position map.
    3. Unicode NFKC-normalized substring: both strings are NFKC-normalized
       (ligatures and compatibility characters unified), then searched; success
       is translated back via a per-character NFKC position map.

    Returns a `CharRange` whose indices ALWAYS refer to positions in the
    original `block_text`, never the normalized form. Returns `None` if all
    three steps fail.

    The matcher is a pure function: no mutation, no shared state. Empty `value`
    returns the vacuous `CharRange(0, 0)`.
    """

    def locate(self, block_text: str, value: str) -> CharRange | None:
        if value == "":
            return CharRange(start=0, end=0)
        if block_text == "":
            return None

        direct = block_text.find(value)
        if direct != -1:
            return CharRange(start=direct, end=direct + len(value))

        whitespace_hit = self._locate_whitespace_collapsed(block_text, value)
        if whitespace_hit is not None:
            return whitespace_hit

        return self._locate_nfkc(block_text, value)

    def _locate_whitespace_collapsed(
        self,
        block_text: str,
        value: str,
    ) -> CharRange | None:
        normalized_block, block_map = _collapse_whitespace_with_map(block_text)
        normalized_value, _ = _collapse_whitespace_with_map(value)

        if normalized_value == "":
            return None

        idx = normalized_block.find(normalized_value)
        if idx == -1:
            return None

        return _translate_range(
            normalized_start=idx,
            normalized_end=idx + len(normalized_value),
            mapping=block_map,
            original_length=len(block_text),
        )

    def _locate_nfkc(
        self,
        block_text: str,
        value: str,
    ) -> CharRange | None:
        normalized_block, block_map = _nfkc_with_map(block_text)
        normalized_value = unicodedata.normalize("NFKC", value)

        if normalized_value == "":
            return None

        idx = normalized_block.find(normalized_value)
        if idx == -1:
            return None

        return _translate_range(
            normalized_start=idx,
            normalized_end=idx + len(normalized_value),
            mapping=block_map,
            original_length=len(block_text),
        )


def _collapse_whitespace_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse runs of whitespace to a single space, tracking origin indices.

    Returns `(normalized_text, mapping)` where `mapping[i]` is the index in
    `text` that contributed the i-th character of `normalized_text`. For a
    collapsed whitespace run, the mapping points at the FIRST character of the
    run in `text` (the "leading edge" convention).
    """
    out_chars: list[str] = []
    mapping: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            out_chars.append(" ")
            mapping.append(i)
            i += 1
            while i < n and text[i].isspace():
                i += 1
        else:
            out_chars.append(ch)
            mapping.append(i)
            i += 1
    return "".join(out_chars), mapping


def _nfkc_with_map(text: str) -> tuple[str, list[int]]:
    """NFKC-normalize `text` while tracking the origin index of each output char.

    NFKC can change character count: `ﬁ` (1 char) → `fi` (2 chars), and
    composed/decomposed accents can merge or split. For every character of the
    normalized output, `mapping[i]` records the index in `text` of the source
    character whose normalization contributed it. When one source character
    expands into multiple normalized characters, all of those normalized
    characters map back to that same source index (leading edge).
    """
    out_parts: list[str] = []
    mapping: list[int] = []
    for i, ch in enumerate(text):
        normalized_ch = unicodedata.normalize("NFKC", ch)
        out_parts.append(normalized_ch)
        mapping.extend([i] * len(normalized_ch))
    return "".join(out_parts), mapping


def _translate_range(
    *,
    normalized_start: int,
    normalized_end: int,
    mapping: list[int],
    original_length: int,
) -> CharRange:
    """Translate a half-open range on the normalized string back to original indices.

    `normalized_start` maps to the origin index of the character at that
    position. `normalized_end` is exclusive: if it points at the end of the
    normalized string, it maps to the end of the original string; otherwise it
    maps to the origin index of the character at `normalized_end`.
    """
    original_start = mapping[normalized_start]
    original_end = original_length if normalized_end >= len(mapping) else mapping[normalized_end]
    return CharRange(start=original_start, end=original_end)

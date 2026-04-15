"""RawExtraction — feature-owned shape produced by ExtractionEngine.

This is the single data type that crosses out of the extraction subpackage
into PDFX-E005's coordinate matching layer. LangExtract's native `Extraction`
must never leak past this boundary, so every field here is explicit and
independent of any third-party class.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RawExtraction:
    """One field extracted by LangExtract, translated into feature-owned shape.

    `field_name` mirrors the key declared in the skill's `output_schema`.
    `value` is the extracted value, or `None` when either:
      (a) the engine emitted a placeholder for a declared field that
          LangExtract did not return (the "every declared field always
          present" API-stability invariant), or
      (b) LangExtract reported a key with no body (degenerate case).
    `char_offset_start` and `char_offset_end` are byte-free integer offsets
    into the `concatenated_text` argument passed to `ExtractionEngine.extract`;
    they are both None for ungrounded values (model world knowledge) and
    for placeholder rows.
    `grounded` summarises whether a source span exists — downstream
    `SpanResolver` uses it to decide whether to look up a bounding box at all.
    Placeholder rows always have `grounded=False`.
    `attempts` is reserved for forward compatibility; LangExtract 1.2.x does
    not surface retry counts, so the engine fills it with 1.
    """

    field_name: str
    value: Any | None
    char_offset_start: int | None
    char_offset_end: int | None
    grounded: bool
    attempts: int

    def __post_init__(self) -> None:
        if self.grounded and (self.char_offset_start is None or self.char_offset_end is None):
            msg = (
                "grounded=True requires both char_offset_start and char_offset_end "
                f"to be set; got start={self.char_offset_start!r}, "
                f"end={self.char_offset_end!r} for field_name={self.field_name!r}"
            )
            raise ValueError(msg)

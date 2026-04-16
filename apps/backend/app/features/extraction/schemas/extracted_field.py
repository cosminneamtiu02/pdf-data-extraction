"""A single extracted field in an extraction response."""

from typing import Any, Literal

from pydantic import BaseModel

from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.field_status import FieldStatus


class ExtractedField(BaseModel):
    """One field of a skill's declared output as produced by the pipeline.

    ``value`` is always ``str | None`` at runtime — the raw string returned
    by LangExtract (``Extraction.extraction_text``), which may be grounded
    to document text when offsets are present, otherwise inferred. The
    ``Any`` annotation is kept for forward compatibility (a future version
    may coerce values to their declared schema type), but callers should
    treat ``value`` as a plain string today.
    ``None`` indicates a declared field that the pipeline could not extract
    (status will be ``failed``).

    The per-field response invariant — every declared field is always present —
    is enforced upstream in the service, not here.
    """

    name: str
    value: Any | None
    status: FieldStatus
    source: Literal["document", "inferred"]
    grounded: bool
    bbox_refs: list[BoundingBoxRef] = []

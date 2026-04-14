"""A single extracted field in an extraction response."""

from typing import Any, Literal

from pydantic import BaseModel

from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.field_status import FieldStatus


class ExtractedField(BaseModel):
    """One field of a skill's declared output as produced by the pipeline.

    ``value`` is typed as ``Any | None`` because each field's concrete type is
    determined by the skill's ``output_schema`` (string, number, date, list,
    object, etc.); the schema layer deliberately does not constrain it. The
    per-field response invariant — every declared field is always present —
    is enforced upstream in the service, not here.
    """

    name: str
    value: Any | None
    status: FieldStatus
    source: Literal["document", "inferred"]
    grounded: bool
    bbox_refs: list[BoundingBoxRef] = []

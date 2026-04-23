"""Shared builders for extraction-pipeline test doubles (issue #404).

Three near-identical ``_make_canned_result`` copies used to live inline in
``tests/contract/_helpers.py``, ``tests/integration/features/extraction/
test_extract_endpoint.py``, and ``tests/integration/scripts/test_benchmark.py``.
When the extraction schemas grew, each copy had to be edited
independently — exactly the kind of drift CLAUDE.md's "one way to do each
thing" rule exists to prevent. The callers vary the field name, the
page count, and whether an annotated-PDF byte blob is attached, but
otherwise produce a structurally identical ``ExtractionResult`` shape.

Callers pass the diverging values as keyword arguments; defaults
reproduce the single-field ``"number"``-shaped happy-path result used by
the contract layer and most integration tests.
"""

from __future__ import annotations

from typing import Literal

from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extract_response import ExtractResponse
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
from app.features.extraction.schemas.field_status import FieldStatus


def _default_bbox() -> BoundingBoxRef:
    """Construct a fresh default ``BoundingBoxRef`` per call.

    A module-level singleton would be reused across every caller, and
    ``BoundingBoxRef`` is a mutable Pydantic model — mutation in one test
    could leak into subsequent calls. Building a new instance per call
    isolates fixtures from each other.
    """
    return BoundingBoxRef(page=1, x0=10.0, y0=20.0, x1=100.0, y1=30.0)


def make_canned_result(  # noqa: PLR0913  # each field maps to a distinct knob; merging loses call-site clarity
    *,
    field_name: str = "number",
    field_value: str = "INV-001",
    page_count: int = 1,
    duration_ms: int = 500,
    skill_name: str = "invoice",
    skill_version: int = 1,
    bbox_refs: list[BoundingBoxRef] | None = None,
    annotated_pdf_bytes: bytes | None = None,
) -> ExtractionResult:
    """Return an ``ExtractionResult`` shaped for the 200 happy path.

    All arguments are keyword-only so adding a new knob is backward-
    compatible. When ``bbox_refs`` is ``None`` the helper falls back to a
    single-bbox default and produces a grounded, document-sourced field;
    pass ``[]`` explicitly to produce an ungrounded, inferred field with
    no bounding boxes. ``grounded`` and ``source`` are always derived
    from whether any refs were supplied so the two shapes stay in lock-
    step with the extraction pipeline's invariant.

    The returned object exercises the same field/metadata/response
    shape the extraction router emits on success; callers override just
    the field name and page count they need for their specific skill
    fixture.
    """
    refs = [_default_bbox()] if bbox_refs is None else bbox_refs
    grounded = bool(refs)
    source: Literal["document", "inferred"] = "document" if grounded else "inferred"
    field = ExtractedField(
        name=field_name,
        value=field_value,
        status=FieldStatus.extracted,
        source=source,
        grounded=grounded,
        bbox_refs=refs,
    )
    metadata = ExtractionMetadata(
        page_count=page_count,
        duration_ms=duration_ms,
        attempts_per_field={field_name: 1},
    )
    response = ExtractResponse(
        skill_name=skill_name,
        skill_version=skill_version,
        fields={field_name: field},
        metadata=metadata,
    )
    return ExtractionResult(response=response, annotated_pdf_bytes=annotated_pdf_bytes)

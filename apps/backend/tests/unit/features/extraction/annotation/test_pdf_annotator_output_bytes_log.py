"""Observability test for PdfAnnotator output-size logging (GH #393).

PyMuPDF's ``Document.tobytes()`` materializes the entire annotated PDF buffer in
Python memory on a worker thread. ``Settings.max_pdf_bytes`` caps the input
side, but highlight annotations can inflate the output beyond the input ceiling.
To let operators watch for that inflation in production, ``_annotate_sync`` must
emit a structlog ``pdf_annotation_output_bytes`` info event carrying both
``output_bytes`` (the length of the ``tobytes()`` result) and ``input_bytes``
(the length of the pre-annotation input).
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pymupdf
from structlog.testing import capture_logs

from app.features.extraction.annotation.pdf_annotator import PdfAnnotator
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.field_status import FieldStatus

_PAGE_WIDTH = 612.0
_PAGE_HEIGHT = 792.0


def _make_blank_pdf(page_count: int = 1) -> bytes:
    with cast("Any", pymupdf.open()) as doc:
        for _ in range(page_count):
            doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        return cast("bytes", doc.tobytes())


def _field(name: str, bbox_refs: list[BoundingBoxRef]) -> ExtractedField:
    return ExtractedField(
        name=name,
        value="placeholder",
        status=FieldStatus.extracted,
        source="document",
        grounded=bool(bbox_refs),
        bbox_refs=bbox_refs,
    )


def test_annotate_emits_output_bytes_log_event_with_positive_size() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)
    fields = [
        _field("x", [BoundingBoxRef(page=1, x0=10, y0=10, x1=50, y1=20)]),
    ]

    with capture_logs() as logs:
        output = asyncio.run(annotator.annotate(pdf_bytes, fields))

    events = [e for e in logs if e.get("event") == "pdf_annotation_output_bytes"]
    assert len(events) == 1, (
        f"expected exactly one pdf_annotation_output_bytes event, got {events!r}"
    )
    event = events[0]
    assert event["output_bytes"] == len(output)
    assert event["output_bytes"] > 0
    assert event["input_bytes"] == len(pdf_bytes)


def test_annotate_emits_output_bytes_log_event_when_no_highlights_drawn() -> None:
    # Even when no annotations are drawn, we still want the observability line
    # so operators can see pass-through output size; the log path must not be
    # gated on whether any highlights were added.
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)

    with capture_logs() as logs:
        output = asyncio.run(annotator.annotate(pdf_bytes, []))

    events = [e for e in logs if e.get("event") == "pdf_annotation_output_bytes"]
    assert len(events) == 1
    event = events[0]
    assert event["output_bytes"] == len(output)
    assert event["output_bytes"] > 0
    assert event["input_bytes"] == len(pdf_bytes)

"""Observability test for PdfAnnotator output-size logging (GH #393).

PyMuPDF's ``Document.tobytes()`` materializes the entire annotated PDF buffer in
Python memory on a worker thread. ``Settings.max_pdf_bytes`` caps the input
side, but highlight annotations can inflate the output beyond the input ceiling.
To let operators watch for that inflation in production, ``_annotate_sync`` must
emit a structlog ``pdf_annotation_output_bytes`` info event carrying both
``output_bytes`` (the length of the ``tobytes()`` result) and ``input_bytes``
(the length of the pre-annotation input).

Uses the ``_SpyLogger`` pattern rather than ``structlog.testing.capture_logs()``
because our ``configure_logging()`` sets ``cache_logger_on_first_use=True`` and
``pdf_annotator`` resolves ``_logger = structlog.get_logger(__name__)`` at
import time — whichever earlier test first touches that logger outside a
``capture_logs()`` context can cache a bound logger that subsequent
``capture_logs()`` contexts won't see, making log-assertion tests order-dependent
(Copilot-review #498). The monkeypatched spy sidesteps structlog's global state
entirely; the same pattern is used in
``tests/unit/features/extraction/test_extraction_service.py``.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pymupdf
import pytest

from app.features.extraction.annotation import pdf_annotator as pdf_annotator_module
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


class _SpyLogger:
    """Test double for ``pdf_annotator_module._logger`` (Copilot-review #498).

    Why we don't use ``structlog.testing.capture_logs()`` here: the annotator
    module defines ``_logger = structlog.get_logger(__name__)`` at import time,
    and our ``configure_logging()`` registers ``cache_logger_on_first_use=True``.
    Whichever earlier test first touches that ``_logger`` outside a
    ``capture_logs()`` context can cause structlog to cache a bound logger
    that subsequent ``capture_logs()`` contexts won't intercept — making
    log-assertion tests order-dependent. A direct monkeypatched spy sidesteps
    structlog's global state entirely, mirroring the pattern in
    ``tests/unit/features/extraction/test_extraction_service.py``.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:  # pragma: no cover
        self.events.append((event, kwargs))

    def error(self, event: str, **kwargs: object) -> None:  # pragma: no cover
        self.events.append((event, kwargs))


def test_annotate_emits_output_bytes_log_event_with_positive_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)
    fields = [
        _field("x", [BoundingBoxRef(page=1, x0=10, y0=10, x1=50, y1=20)]),
    ]

    spy = _SpyLogger()
    monkeypatch.setattr(pdf_annotator_module, "_logger", spy)

    output = asyncio.run(annotator.annotate(pdf_bytes, fields))

    events = [kwargs for event, kwargs in spy.events if event == "pdf_annotation_output_bytes"]
    assert len(events) == 1, (
        f"expected exactly one pdf_annotation_output_bytes event, got {spy.events!r}"
    )
    event = events[0]
    assert event["output_bytes"] == len(output)
    assert cast("int", event["output_bytes"]) > 0
    assert event["input_bytes"] == len(pdf_bytes)


def test_annotate_emits_output_bytes_log_event_when_no_highlights_drawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even when no annotations are drawn, we still want the observability line
    # so operators can see pass-through output size; the log path must not be
    # gated on whether any highlights were added.
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)

    spy = _SpyLogger()
    monkeypatch.setattr(pdf_annotator_module, "_logger", spy)

    output = asyncio.run(annotator.annotate(pdf_bytes, []))

    events = [kwargs for event, kwargs in spy.events if event == "pdf_annotation_output_bytes"]
    assert len(events) == 1
    event = events[0]
    assert event["output_bytes"] == len(output)
    assert cast("int", event["output_bytes"]) > 0
    assert event["input_bytes"] == len(pdf_bytes)

"""Unit tests for PdfAnnotator (PDFX-E006-F004).

Scenarios map 1:1 to the `## Unit test scenarios` section of
docs/graphs/PDFX/PDFX-E006-F004.md.

Important: PyMuPDF's SWIG bindings make `Annot` objects hold non-owning
references to their parent `Page`. If a helper returns a list of annots and
then goes out of scope, the page is GC'd and subsequent `annot.rect` access
segfaults. Therefore every assertion on annotation data is performed in the
same lexical scope that holds the `page` local — do NOT factor the
"get annots on page N" pattern into a helper.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from typing import TYPE_CHECKING, Any, cast

import pymupdf

from app.features.extraction.annotation.pdf_annotator import PdfAnnotator
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.field_status import FieldStatus

if TYPE_CHECKING:
    from types import TracebackType

    import pytest

_RECT_TOLERANCE = 8.0  # PyMuPDF expands highlight rects to cover quadpoints; observed drift ~5px
_PDF_ANNOT_HIGHLIGHT = 8


def _field(name: str, bbox_refs: list[BoundingBoxRef]) -> ExtractedField:
    return ExtractedField(
        name=name,
        value="placeholder",
        status=FieldStatus.extracted,
        source="document",
        grounded=bool(bbox_refs),
        bbox_refs=bbox_refs,
    )


def _make_blank_pdf(page_count: int = 2) -> bytes:
    doc: Any = cast("Any", pymupdf.open())
    for _ in range(page_count):
        doc.new_page(width=612.0, height=792.0)
    data: bytes = cast("bytes", doc.tobytes())
    doc.close()
    return data


def _run(annotator: PdfAnnotator, pdf_bytes: bytes, fields: list[ExtractedField]) -> bytes:
    return asyncio.run(annotator.annotate(pdf_bytes, fields))


def _rect_contains(
    container: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    tolerance: float = _RECT_TOLERANCE,
) -> bool:
    return (
        container[0] <= inner[0] + tolerance
        and container[1] <= inner[1] + tolerance
        and container[2] >= inner[2] - tolerance
        and container[3] >= inner[3] - tolerance
    )


def test_single_field_single_bbox_single_page_draws_one_highlight() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=2)
    input_rect = (10.0, 10.0, 100.0, 30.0)
    fields = [
        _field(
            "x",
            [
                BoundingBoxRef(
                    page=1, x0=input_rect[0], y0=input_rect[1], x1=input_rect[2], y1=input_rect[3]
                )
            ],
        ),
    ]

    output = _run(annotator, pdf_bytes, fields)

    doc: Any = cast("Any", pymupdf.open(stream=output, filetype="pdf"))
    try:
        page0 = doc[0]
        page1 = doc[1]
        page0_rects: list[tuple[float, float, float, float]] = []
        for a in page0.annots() or []:
            r = a.rect
            page0_rects.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))
        page1_count = sum(1 for _ in (page1.annots() or []))
        assert len(page0_rects) == 1
        assert page1_count == 0
        assert _rect_contains(page0_rects[0], input_rect)
    finally:
        doc.close()


def test_empty_bbox_refs_are_skipped_silently() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)
    fields = [
        _field("a", [BoundingBoxRef(page=1, x0=10, y0=10, x1=50, y1=20)]),
        _field("b", []),
        _field("c", [BoundingBoxRef(page=1, x0=60, y0=60, x1=120, y1=80)]),
    ]

    output = _run(annotator, pdf_bytes, fields)

    doc: Any = cast("Any", pymupdf.open(stream=output, filetype="pdf"))
    try:
        page = doc[0]
        count = sum(1 for _ in (page.annots() or []))
        assert count == 2
    finally:
        doc.close()


def test_multi_page_span_draws_one_annotation_per_page() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=2)
    rect_a = (10.0, 10.0, 100.0, 30.0)
    rect_b = (20.0, 40.0, 150.0, 60.0)
    fields = [
        _field(
            "span",
            [
                BoundingBoxRef(page=1, x0=rect_a[0], y0=rect_a[1], x1=rect_a[2], y1=rect_a[3]),
                BoundingBoxRef(page=2, x0=rect_b[0], y0=rect_b[1], x1=rect_b[2], y1=rect_b[3]),
            ],
        ),
    ]

    output = _run(annotator, pdf_bytes, fields)

    doc: Any = cast("Any", pymupdf.open(stream=output, filetype="pdf"))
    try:
        page0 = doc[0]
        page1 = doc[1]
        page0_rects: list[tuple[float, float, float, float]] = []
        for a in page0.annots() or []:
            r = a.rect
            page0_rects.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))
        page1_rects: list[tuple[float, float, float, float]] = []
        for a in page1.annots() or []:
            r = a.rect
            page1_rects.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))
        assert len(page0_rects) == 1
        assert len(page1_rects) == 1
        assert _rect_contains(page0_rects[0], rect_a)
        assert _rect_contains(page1_rects[0], rect_b)
    finally:
        doc.close()


def test_multi_block_span_on_same_page_draws_all_highlights() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)
    input_rects = [
        (10.0, 10.0, 100.0, 20.0),
        (10.0, 40.0, 100.0, 50.0),
        (10.0, 70.0, 100.0, 80.0),
        (10.0, 100.0, 100.0, 110.0),
        (10.0, 130.0, 100.0, 140.0),
    ]
    fields = [
        _field(
            "multi",
            [BoundingBoxRef(page=1, x0=r[0], y0=r[1], x1=r[2], y1=r[3]) for r in input_rects],
        ),
    ]

    output = _run(annotator, pdf_bytes, fields)

    doc: Any = cast("Any", pymupdf.open(stream=output, filetype="pdf"))
    try:
        page = doc[0]
        observed: list[tuple[float, float, float, float]] = []
        for a in page.annots() or []:
            r = a.rect
            observed.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))
        assert len(observed) == 5
        for expected in input_rects:
            assert any(_rect_contains(obs, expected) for obs in observed), (
                f"no observed annotation contains expected rect {expected}"
            )
    finally:
        doc.close()


def test_empty_fields_list_returns_valid_unannotated_pdf() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=3)

    output = _run(annotator, pdf_bytes, [])

    doc: Any = cast("Any", pymupdf.open(stream=output, filetype="pdf"))
    try:
        assert doc.page_count == 3
        for i in range(3):
            page = doc[i]
            assert sum(1 for _ in (page.annots() or [])) == 0
    finally:
        doc.close()


def test_input_bytes_are_not_mutated() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)
    digest_before = hashlib.sha256(pdf_bytes).hexdigest()

    _run(
        annotator,
        pdf_bytes,
        [_field("x", [BoundingBoxRef(page=1, x0=10, y0=10, x1=50, y1=20)])],
    )

    assert hashlib.sha256(pdf_bytes).hexdigest() == digest_before


def test_output_is_not_byte_identical_to_input_when_annotations_drawn() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)

    output = _run(
        annotator,
        pdf_bytes,
        [_field("x", [BoundingBoxRef(page=1, x0=10, y0=10, x1=50, y1=20)])],
    )

    assert output != pdf_bytes


def test_drawn_annotation_is_highlight_subtype() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)

    output = _run(
        annotator,
        pdf_bytes,
        [_field("x", [BoundingBoxRef(page=1, x0=10, y0=10, x1=50, y1=20)])],
    )

    doc: Any = cast("Any", pymupdf.open(stream=output, filetype="pdf"))
    try:
        page = doc[0]
        types = [int(a.type[0]) for a in page.annots() or []]
        assert types == [_PDF_ANNOT_HIGHLIGHT]
    finally:
        doc.close()


def test_zero_area_bbox_does_not_raise_and_is_skipped() -> None:
    # The BoundingBoxRef schema permits zero-area rects (degenerate spans are valid
    # grounding anchors), but PyMuPDF rejects them for highlight quads. Annotator
    # contract: silently skip, mirroring the empty-bbox_refs behaviour — never raise.
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)

    output = _run(
        annotator,
        pdf_bytes,
        [_field("x", [BoundingBoxRef(page=1, x0=50, y0=50, x1=50, y1=50)])],
    )

    doc: Any = cast("Any", pymupdf.open(stream=output, filetype="pdf"))
    try:
        page = doc[0]
        count = sum(1 for _ in (page.annots() or []))
        assert count == 0
    finally:
        doc.close()


def test_multiple_fields_on_distinct_pages_in_3_page_doc() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=3)
    fields = [
        _field("f1", [BoundingBoxRef(page=1, x0=10, y0=10, x1=50, y1=20)]),
        _field("f2", [BoundingBoxRef(page=2, x0=20, y0=20, x1=60, y1=30)]),
        _field("f3", [BoundingBoxRef(page=3, x0=30, y0=30, x1=70, y1=40)]),
    ]

    output = _run(annotator, pdf_bytes, fields)

    doc: Any = cast("Any", pymupdf.open(stream=output, filetype="pdf"))
    try:
        for i in range(3):
            page = doc[i]
            assert sum(1 for _ in (page.annots() or [])) == 1
    finally:
        doc.close()


def test_annotate_is_async_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(PdfAnnotator.annotate)


def test_document_handle_is_closed_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # Build the input PDF BEFORE installing the spy; the spy wraps every
    # pymupdf.open call and we only want to count the annotator's usage.
    pdf_bytes = _make_blank_pdf(page_count=1)

    close_calls: list[int] = []
    real_open = cast("Any", pymupdf.open)

    class _ClosingSpy:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __enter__(self) -> Any:
            return self._inner.__enter__()

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> Any:
            close_calls.append(1)
            return self._inner.__exit__(exc_type, exc, tb)

    def _spy_open(*args: Any, **kwargs: Any) -> Any:
        inner = real_open(*args, **kwargs)
        return _ClosingSpy(inner)

    monkeypatch.setattr(pymupdf, "open", _spy_open)

    annotator = PdfAnnotator()
    _run(
        annotator,
        pdf_bytes,
        [_field("x", [BoundingBoxRef(page=1, x0=10, y0=10, x1=50, y1=20)])],
    )

    assert sum(close_calls) == 1

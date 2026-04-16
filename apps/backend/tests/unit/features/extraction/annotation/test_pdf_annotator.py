"""Unit tests for PdfAnnotator (PDFX-E006-F004).

Scenarios map 1:1 to the `## Unit test scenarios` section of
docs/graphs/PDFX/PDFX-E006-F004.md.

Two PyMuPDF gotchas these tests work around:

1. SWIG lifetime. PyMuPDF `Annot` objects hold non-owning references to their
   parent `Page`. If a helper returns a list of annots and then goes out of
   scope, the page is GC'd and subsequent `annot.rect` reads segfault. Every
   assertion on annotation data is therefore performed in the same lexical
   scope that holds the `page` local — do NOT factor the "get annots on page
   N" pattern into a helper.

2. Coordinate origin. `BoundingBoxRef` is bottom-left PDF-native, but
   PyMuPDF's drawing and `Annot.rect` APIs use top-left MuPDF coordinates.
   `PdfAnnotator._draw_highlight` flips `y` via `page_height - y`, so the
   expected MuPDF-view rect for a bottom-left bbox `(x0, y0, x1, y1)` on a
   page of height `h` is `(x0, h - y1, x1, h - y0)`. Helper
   `_expected_mupdf_rect` centralizes this conversion.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pymupdf
import pytest

from app.features.extraction.annotation.pdf_annotator import PdfAnnotator
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.field_status import FieldStatus

if TYPE_CHECKING:
    from types import TracebackType

_PAGE_HEIGHT = 792.0
_PAGE_WIDTH = 612.0
_RECT_TOLERANCE = 8.0  # PyMuPDF expands highlight rects to cover quadpoints; observed drift ~5px
_PDF_ANNOT_HIGHLIGHT = 8


def _rect_matches_within_tolerance(
    observed: tuple[float, float, float, float],
    expected: tuple[float, float, float, float],
    tolerance: float = _RECT_TOLERANCE,
) -> bool:
    # Symmetric per-edge proximity check. Rejects any shift larger than `tolerance`
    # on any edge — unlike a one-sided containment predicate that would let a
    # shrunken or drifted rect pass.
    return (
        abs(observed[0] - expected[0]) <= tolerance
        and abs(observed[1] - expected[1]) <= tolerance
        and abs(observed[2] - expected[2]) <= tolerance
        and abs(observed[3] - expected[3]) <= tolerance
    )


def _expected_mupdf_rect(
    bl_rect: tuple[float, float, float, float],
    page_height: float = _PAGE_HEIGHT,
) -> tuple[float, float, float, float]:
    """Flip a bottom-left `(x0, y0, x1, y1)` rect into PyMuPDF's top-left view."""
    x0, y0, x1, y1 = bl_rect
    return (x0, page_height - y1, x1, page_height - y0)


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
    with cast("Any", pymupdf.open()) as doc:
        for _ in range(page_count):
            doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        return cast("bytes", doc.tobytes())


def _run(annotator: PdfAnnotator, pdf_bytes: bytes, fields: list[ExtractedField]) -> bytes:
    return asyncio.run(annotator.annotate(pdf_bytes, fields))


def test_single_field_single_bbox_single_page_draws_one_highlight() -> None:
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=2)
    bl_rect = (10.0, 10.0, 100.0, 30.0)
    fields = [
        _field(
            "x",
            [BoundingBoxRef(page=1, x0=bl_rect[0], y0=bl_rect[1], x1=bl_rect[2], y1=bl_rect[3])],
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
        assert _rect_matches_within_tolerance(page0_rects[0], _expected_mupdf_rect(bl_rect))
    finally:
        doc.close()


def test_bottom_left_bbox_near_floor_renders_in_bottom_half_of_page() -> None:
    """Visual-placement regression guard for the bottom-left → top-left flip.

    A bbox described in bottom-left coordinates with `y0=10, y1=30` (10-30 px
    above the bottom edge) must end up in the BOTTOM half of a 792 pt page when
    read back through PyMuPDF's top-left view, not at the top. Before the
    coordinate flip landed, `PdfAnnotator` passed bottom-left y values directly
    into `pymupdf.Rect`, which silently rendered this bbox at the top of the
    page. This test would fail against that regression.
    """
    annotator = PdfAnnotator()
    pdf_bytes = _make_blank_pdf(page_count=1)
    bl_rect = (20.0, 10.0, 120.0, 30.0)
    fields = [
        _field(
            "floor",
            [BoundingBoxRef(page=1, x0=bl_rect[0], y0=bl_rect[1], x1=bl_rect[2], y1=bl_rect[3])],
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
        assert len(observed) == 1
        y_top, y_bot = observed[0][1], observed[0][3]
        # Bottom half of an 792 pt page in MuPDF top-left view = y > 396.
        assert y_top > _PAGE_HEIGHT / 2, (
            f"highlight top edge {y_top} is in the upper half of the page; "
            f"coordinate flip regression suspected"
        )
        assert y_bot > _PAGE_HEIGHT / 2
        assert _rect_matches_within_tolerance(observed[0], _expected_mupdf_rect(bl_rect))
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
    bl_a = (10.0, 10.0, 100.0, 30.0)
    bl_b = (20.0, 40.0, 150.0, 60.0)
    fields = [
        _field(
            "span",
            [
                BoundingBoxRef(page=1, x0=bl_a[0], y0=bl_a[1], x1=bl_a[2], y1=bl_a[3]),
                BoundingBoxRef(page=2, x0=bl_b[0], y0=bl_b[1], x1=bl_b[2], y1=bl_b[3]),
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
        assert _rect_matches_within_tolerance(page0_rects[0], _expected_mupdf_rect(bl_a))
        assert _rect_matches_within_tolerance(page1_rects[0], _expected_mupdf_rect(bl_b))
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
        for expected_bl in input_rects:
            expected_mupdf = _expected_mupdf_rect(expected_bl)
            assert any(_rect_matches_within_tolerance(obs, expected_mupdf) for obs in observed), (
                f"no observed annotation rect matched expected MuPDF-view rect "
                f"{expected_mupdf} (from bottom-left input {expected_bl}) within "
                f"tolerance {_RECT_TOLERANCE}"
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


@pytest.mark.asyncio
async def test_annotate_does_not_block_event_loop_during_pymupdf_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If blocking PyMuPDF work is offloaded via asyncio.to_thread, a parallel
    coroutine must make progress while annotate runs.

    A 1-page blank PDF annotates in microseconds, too fast for the ticker to
    advance even with correct offloading. We wrap ``pymupdf.open`` with a
    synthetic 200ms ``time.sleep`` so ``annotate`` takes long enough that the
    ticker should advance if the work is offloaded. If ``annotate`` were
    still running on the event loop thread, that sleep would block the ticker
    completely and ``ticks`` would stay at 0.
    """
    import time

    pdf_bytes = _make_blank_pdf(page_count=1)
    real_open = cast("Any", pymupdf.open)

    def _slow_open(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.2)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(pymupdf, "open", _slow_open)

    annotator = PdfAnnotator()
    fields = [
        _field(
            "x",
            [BoundingBoxRef(page=1, x0=10, y0=10, x1=50, y1=20)],
        ),
    ]

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while ticks < 500:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker_task = asyncio.create_task(_ticker())
    try:
        await annotator.annotate(pdf_bytes, fields)
    finally:
        ticker_task.cancel()
        with _suppress_cancelled():
            await ticker_task

    # 200ms of blocking work offloaded to a thread should leave room for the
    # ticker to fire at least ~10 times. We assert 5 as a generous floor to
    # avoid flakes on slow CI while still catching a regression that would put
    # the sleep on the event loop (which would leave ticks == 0).
    assert ticks >= 5, (
        f"ticker did not advance enough (ticks={ticks}); annotate appears to block the event loop"
    )


def _suppress_cancelled() -> Any:
    import contextlib

    return contextlib.suppress(asyncio.CancelledError)


_ALLOWED_FITZ_IMPORTERS: frozenset[str] = frozenset(
    {
        "annotation/pdf_annotator.py",
        # parsing/docling_document_parser.py may preflight via PyMuPDF once PDFX-E003-F002 lands.
        "parsing/docling_document_parser.py",
    }
)
_FITZ_ROOTS: frozenset[str] = frozenset({"fitz", "pymupdf"})


def _collect_root_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_pymupdf_imports_are_confined_to_pdf_annotator() -> None:
    extraction_root = Path(__file__).resolve().parents[5] / "app" / "features" / "extraction"
    assert extraction_root.is_dir(), f"extraction feature root not found at {extraction_root}"

    offenders: list[str] = []
    for py_file in extraction_root.rglob("*.py"):
        relative = py_file.relative_to(extraction_root).as_posix()
        roots = _collect_root_imports(py_file.read_text())
        if roots & _FITZ_ROOTS and relative not in _ALLOWED_FITZ_IMPORTERS:
            offenders.append(relative)

    assert not offenders, (
        f"PyMuPDF imports must be confined to {sorted(_ALLOWED_FITZ_IMPORTERS)}, "
        f"but found in: {sorted(offenders)}"
    )


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

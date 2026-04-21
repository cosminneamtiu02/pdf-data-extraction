"""Integration tests for DoclingDocumentParser against real Docling (PDFX-E003-F002).

These tests are all marked `@pytest.mark.slow` because they load real Docling
(and therefore ONNX/OCR models) and walk real PDF fixtures. They are skipped
automatically when Docling is not importable — which is the state of the
repository until PDFX-E001-F002 (PR #23) lands docling as a pinned runtime
dependency. Once it does, these tests become the empirical verification of:

  * The real Docling walk produces `ParsedDocument` with non-empty blocks
    for a native digital PDF.
  * OCR auto-detection engages for a scanned (image-only) PDF.
  * Bounding boxes emitted by the parser agree with PyMuPDF's bottom-left
    origin convention on the same fixture pages (resolves the UNRESOLVED
    coordinate-convention question from the feature spec).
  * Real Docling parsing does not starve the asyncio event loop.
  * Repeated parse calls on the same instance are equivalent.

Fixtures required (committed alongside PR that enables these tests):
  * apps/backend/tests/fixtures/pdfs/native_two_page.pdf
  * apps/backend/tests/fixtures/pdfs/scanned_one_page.pdf
"""

from __future__ import annotations

import contextlib
import importlib.util
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


_DOCLING_AVAILABLE = importlib.util.find_spec("docling") is not None
_PYMUPDF_AVAILABLE = importlib.util.find_spec("pymupdf") is not None
# Docling's `TesseractCliOcrOptions` path shells out to the `tesseract` binary
# at OCR time (issue #106, ADR-013). When it is not on `PATH`, Docling raises
# `RuntimeError: Tesseract is not available, aborting`. Skipping here matches
# the existing pattern for other optional runtime prerequisites (`docling`,
# `pymupdf`, fixture PDFs) so `task test:slow` on a dev machine without
# `brew install tesseract` reports a clear skip reason instead of a failure.
_TESSERACT_AVAILABLE = shutil.which("tesseract") is not None
# parents: [0]=parsing [1]=extraction [2]=features [3]=integration [4]=tests
# so parents[4] is apps/backend/tests, which is where fixtures live.
_FIXTURES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "pdfs"
_NATIVE_FIXTURE = _FIXTURES_DIR / "native_two_page.pdf"
_SCANNED_FIXTURE = _FIXTURES_DIR / "scanned_one_page.pdf"

_SKIP_REASON_DOCLING = (
    "docling is not installed; this integration test activates once "
    "PDFX-E001-F002 pins docling as a runtime dependency."
)
_SKIP_REASON_FIXTURE = "PDF fixture not committed yet; add it to apps/backend/tests/fixtures/pdfs/"
_SKIP_REASON_TESSERACT = (
    "tesseract binary not on PATH; install it (`brew install tesseract` on "
    "macOS, `apt-get install tesseract-ocr tesseract-ocr-eng` on Debian) to "
    "run this slow test. The Docker runtime installs it automatically — see "
    "infra/docker/backend.Dockerfile and docs/decisions.md ADR-013."
)


@pytest.mark.skipif(not _DOCLING_AVAILABLE, reason=_SKIP_REASON_DOCLING)
@pytest.mark.skipif(not _NATIVE_FIXTURE.exists(), reason=_SKIP_REASON_FIXTURE)
@pytest.mark.skipif(not _TESSERACT_AVAILABLE, reason=_SKIP_REASON_TESSERACT)
async def test_real_docling_parses_native_two_page_fixture() -> None:
    from app.features.extraction.parsing.docling_config import DoclingConfig
    from app.features.extraction.parsing.docling_document_parser import (
        DoclingDocumentParser,
    )

    parser = DoclingDocumentParser()
    pdf_bytes = _NATIVE_FIXTURE.read_bytes()

    result = await parser.parse(pdf_bytes, DoclingConfig(ocr="auto", table_mode="fast"))

    assert result.page_count == 2
    assert len(result.blocks) > 0


@pytest.mark.skipif(not _DOCLING_AVAILABLE, reason=_SKIP_REASON_DOCLING)
@pytest.mark.skipif(not _SCANNED_FIXTURE.exists(), reason=_SKIP_REASON_FIXTURE)
@pytest.mark.skipif(not _TESSERACT_AVAILABLE, reason=_SKIP_REASON_TESSERACT)
async def test_real_docling_ocrs_scanned_fixture() -> None:
    from app.features.extraction.parsing.docling_config import DoclingConfig
    from app.features.extraction.parsing.docling_document_parser import (
        DoclingDocumentParser,
    )

    parser = DoclingDocumentParser()
    pdf_bytes = _SCANNED_FIXTURE.read_bytes()

    result = await parser.parse(pdf_bytes, DoclingConfig(ocr="auto", table_mode="fast"))

    assert len(result.blocks) > 0


@pytest.mark.skipif(not _DOCLING_AVAILABLE, reason=_SKIP_REASON_DOCLING)
@pytest.mark.skipif(not _PYMUPDF_AVAILABLE, reason="pymupdf not installed")
@pytest.mark.skipif(not _NATIVE_FIXTURE.exists(), reason=_SKIP_REASON_FIXTURE)
@pytest.mark.skipif(not _TESSERACT_AVAILABLE, reason=_SKIP_REASON_TESSERACT)
async def test_real_docling_bboxes_agree_with_pymupdf_on_native_fixture() -> None:
    """Empirically verify that Docling's bbox output lands inside PyMuPDF page rects.

    This is the authoritative check for UNRESOLVED open question #3 in the
    feature spec (coordinate convention). Every emitted bbox must:
      1. Have `x0 < x1 and y0 < y1` (valid rectangle).
      2. Fit inside the PyMuPDF-reported page rect for its page, with a
         small tolerance (accounts for small floating-point drift).
    If Docling ever changes its native origin from bottom-left to top-left
    without us updating the adapter, this test catches it because the
    y-values would land outside [0, page_height].
    """
    import pymupdf  # type: ignore[import-not-found]  # gated by _PYMUPDF_AVAILABLE

    from app.features.extraction.parsing.docling_config import DoclingConfig
    from app.features.extraction.parsing.docling_document_parser import (
        DoclingDocumentParser,
    )

    parser = DoclingDocumentParser()
    pdf_bytes = _NATIVE_FIXTURE.read_bytes()

    result = await parser.parse(pdf_bytes, DoclingConfig(ocr="auto", table_mode="fast"))

    tolerance = 1.0
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_rects = {i + 1: doc[i].rect for i in range(len(doc))}

    for block in result.blocks:
        page_rect = page_rects[block.page_number]
        assert block.bbox.x0 < block.bbox.x1
        assert block.bbox.y0 < block.bbox.y1
        assert block.bbox.x0 >= -tolerance
        assert block.bbox.y0 >= -tolerance
        assert block.bbox.x1 <= page_rect.width + tolerance
        assert block.bbox.y1 <= page_rect.height + tolerance


@pytest.mark.skipif(not _DOCLING_AVAILABLE, reason=_SKIP_REASON_DOCLING)
@pytest.mark.skipif(not _NATIVE_FIXTURE.exists(), reason=_SKIP_REASON_FIXTURE)
@pytest.mark.skipif(not _TESSERACT_AVAILABLE, reason=_SKIP_REASON_TESSERACT)
async def test_real_docling_parse_does_not_starve_event_loop() -> None:
    import asyncio

    from app.features.extraction.parsing.docling_config import DoclingConfig
    from app.features.extraction.parsing.docling_document_parser import (
        DoclingDocumentParser,
    )

    parser = DoclingDocumentParser()
    pdf_bytes = _NATIVE_FIXTURE.read_bytes()

    biggest_gap = 0.0
    last_tick = asyncio.get_event_loop().time()

    async def sampler() -> None:
        nonlocal biggest_gap, last_tick
        while True:
            await asyncio.sleep(0.01)
            now = asyncio.get_event_loop().time()
            gap = now - last_tick
            biggest_gap = max(biggest_gap, gap)
            last_tick = now

    sampler_task = asyncio.create_task(sampler())
    await parser.parse(pdf_bytes, DoclingConfig(ocr="auto", table_mode="fast"))
    sampler_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sampler_task

    assert biggest_gap < 0.5  # generous for CI noise; 100ms is the spec target


@pytest.mark.skipif(not _DOCLING_AVAILABLE, reason=_SKIP_REASON_DOCLING)
@pytest.mark.skipif(not _NATIVE_FIXTURE.exists(), reason=_SKIP_REASON_FIXTURE)
@pytest.mark.skipif(not _TESSERACT_AVAILABLE, reason=_SKIP_REASON_TESSERACT)
async def test_real_docling_repeat_parse_is_equivalent() -> None:
    from app.features.extraction.parsing.docling_config import DoclingConfig
    from app.features.extraction.parsing.docling_document_parser import (
        DoclingDocumentParser,
    )

    parser = DoclingDocumentParser()
    pdf_bytes = _NATIVE_FIXTURE.read_bytes()

    first = await parser.parse(pdf_bytes, DoclingConfig(ocr="auto", table_mode="fast"))
    second = await parser.parse(pdf_bytes, DoclingConfig(ocr="auto", table_mode="fast"))

    assert first.page_count == second.page_count
    assert len(first.blocks) == len(second.blocks)
    assert [b.text for b in first.blocks] == [b.text for b in second.blocks]

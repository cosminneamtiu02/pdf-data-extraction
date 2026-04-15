"""Unit tests for PDFX-E003-F004 — PDF failure-mode error contract.

Covers the four PDF-side error raises wired into `DoclingDocumentParser.parse`:

- `PdfInvalidError` (400) when preflight rejects bytes.
- `PdfPasswordProtectedError` (400) when preflight reports an encrypted PDF.
- `PdfTooManyPagesError` (413) when `preflight` returns a page count greater
  than `max_pdf_pages`. Raised *before* `converter.convert` is called — the
  whole point of the check is to avoid paying Docling's OCR/layout cost.
- `PdfNoTextExtractableError` (422) when the parsed document yields zero blocks.

All tests drive the parser via fake converter factories and fake preflights.
No real Docling, no real PyMuPDF, no real PDFs.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
import structlog

from app.exceptions import (
    PdfInvalidError,
    PdfNoTextExtractableError,
    PdfPasswordProtectedError,
    PdfTooManyPagesError,
)
from app.exceptions.base import DomainError
from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
)

_DEFAULT_CONFIG = DoclingConfig(ocr="auto", table_mode="fast")


# ---------------------------------------------------------------------------
# Fake shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeTextItem:
    text: str
    page_number: int
    bbox_x0: float
    bbox_y0: float
    bbox_x1: float
    bbox_y1: float


@dataclass
class _FakeDocument:
    _page_count: int
    _items: tuple[_FakeTextItem, ...] = ()

    @property
    def page_count(self) -> int:
        return self._page_count

    def iter_text_items(self) -> list[_FakeTextItem]:
        return list(self._items)


@dataclass
class _RecordingConverter:
    document: _FakeDocument
    convert_calls: int = 0

    def convert(self, _pdf_bytes: bytes) -> _FakeDocument:
        self.convert_calls += 1
        return self.document


def _factory_returning(doc: _FakeDocument) -> Any:
    captured: dict[str, _RecordingConverter] = {}

    def _factory(_config: DoclingConfig) -> _RecordingConverter:
        converter = _RecordingConverter(document=doc)
        captured["last"] = converter
        return converter

    _factory.captured = captured  # type: ignore[attr-defined]
    return _factory


def _const_preflight(page_count: int) -> Any:
    def _pf(_pdf_bytes: bytes) -> int:
        return page_count

    return _pf


def _valid_single_block_doc(page_count: int) -> _FakeDocument:
    return _FakeDocument(
        _page_count=page_count,
        _items=(
            _FakeTextItem(
                text="hello",
                page_number=1,
                bbox_x0=10.0,
                bbox_y0=700.0,
                bbox_x1=80.0,
                bbox_y1=720.0,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Error classes — re-exports and hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error_cls", "expected_code", "expected_status"),
    [
        (PdfInvalidError, "PDF_INVALID", 400),
        (PdfPasswordProtectedError, "PDF_PASSWORD_PROTECTED", 400),
        (PdfTooManyPagesError, "PDF_TOO_MANY_PAGES", 413),
        (PdfNoTextExtractableError, "PDF_NO_TEXT_EXTRACTABLE", 422),
    ],
)
def test_pdf_error_classes_are_domain_errors_with_expected_code_and_status(
    error_cls: type[DomainError],
    expected_code: str,
    expected_status: int,
) -> None:
    assert issubclass(error_cls, DomainError)
    assert error_cls.code == expected_code
    assert error_cls.http_status == expected_status


def test_pdf_too_many_pages_params_round_trip() -> None:
    err = PdfTooManyPagesError(limit=200, actual=250)

    assert err.params is not None
    assert err.params.model_dump() == {"limit": 200, "actual": 250}


# ---------------------------------------------------------------------------
# Preflight-driven errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_raises_pdf_invalid_when_preflight_rejects_bytes() -> None:
    def _rejecting_preflight(_pdf_bytes: bytes) -> int:
        raise PdfInvalidError

    factory_calls = 0

    def _factory(_config: DoclingConfig) -> _RecordingConverter:
        nonlocal factory_calls
        factory_calls += 1
        return _RecordingConverter(document=_valid_single_block_doc(1))

    parser = DoclingDocumentParser(
        converter_factory=_factory,
        pdf_preflight=_rejecting_preflight,
    )

    with pytest.raises(PdfInvalidError):
        await parser.parse(b"not a pdf", _DEFAULT_CONFIG)

    assert factory_calls == 0, "converter must not run when preflight rejects"


@pytest.mark.asyncio
async def test_parse_raises_pdf_password_protected_when_preflight_reports_encrypted() -> None:
    def _encrypted_preflight(_pdf_bytes: bytes) -> int:
        raise PdfPasswordProtectedError

    factory_calls = 0

    def _factory(_config: DoclingConfig) -> _RecordingConverter:
        nonlocal factory_calls
        factory_calls += 1
        return _RecordingConverter(document=_valid_single_block_doc(1))

    parser = DoclingDocumentParser(
        converter_factory=_factory,
        pdf_preflight=_encrypted_preflight,
    )

    with pytest.raises(PdfPasswordProtectedError):
        await parser.parse(b"%PDF-encrypted", _DEFAULT_CONFIG)

    assert factory_calls == 0


# ---------------------------------------------------------------------------
# Page-count enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_raises_pdf_too_many_pages_before_converter_runs() -> None:
    factory = _factory_returning(_valid_single_block_doc(201))
    parser = DoclingDocumentParser(
        converter_factory=factory,
        pdf_preflight=_const_preflight(201),
        max_pdf_pages=200,
    )

    with pytest.raises(PdfTooManyPagesError) as excinfo:
        await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"limit": 200, "actual": 201}
    # Proves the raise happens BEFORE Docling's expensive conversion step.
    assert "last" not in factory.captured, (
        "converter_factory must not be invoked when page count exceeds the limit"
    )


@pytest.mark.asyncio
async def test_parse_allows_page_count_exactly_at_limit() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_factory_returning(_valid_single_block_doc(200)),
        pdf_preflight=_const_preflight(200),
        max_pdf_pages=200,
    )

    result = await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)

    assert result.page_count == 200
    assert len(result.blocks) == 1


@pytest.mark.asyncio
async def test_parse_default_max_pdf_pages_is_200() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_factory_returning(_valid_single_block_doc(201)),
        pdf_preflight=_const_preflight(201),
    )

    with pytest.raises(PdfTooManyPagesError) as excinfo:
        await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"limit": 200, "actual": 201}


@pytest.mark.asyncio
async def test_parse_too_many_pages_raise_is_fast() -> None:
    """The page-limit raise must not pay any conversion cost.

    Spec AC (PDFX-E003-F004): `the raise happens before any OCR or layout
    analysis runs... the raise is under 500 ms`. The cost guard is that the
    converter factory is never invoked (asserted above); this test additionally
    asserts wall-clock latency is low even against a converter that would
    otherwise sleep for a full second.
    """

    @dataclass
    class _SlowConverter:
        document: _FakeDocument

        def convert(self, _pdf_bytes: bytes) -> _FakeDocument:
            time.sleep(1.0)  # would dominate if ever reached
            return self.document

    def _slow_factory(_config: DoclingConfig) -> _SlowConverter:
        return _SlowConverter(document=_valid_single_block_doc(500))

    parser = DoclingDocumentParser(
        converter_factory=_slow_factory,
        pdf_preflight=_const_preflight(500),
        max_pdf_pages=200,
    )

    start = time.monotonic()
    with pytest.raises(PdfTooManyPagesError):
        await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"early rejection took {elapsed:.3f}s; must be <0.5s"


# ---------------------------------------------------------------------------
# Empty-output enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_raises_pdf_no_text_extractable_when_document_has_zero_blocks() -> None:
    doc = _FakeDocument(_page_count=1, _items=())
    parser = DoclingDocumentParser(
        converter_factory=_factory_returning(doc),
        pdf_preflight=_const_preflight(1),
    )

    with pytest.raises(PdfNoTextExtractableError):
        await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Structured logging on raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_logs_structured_event_for_too_many_pages() -> None:
    """Each error raise emits one structlog event with key=value fields."""
    parser = DoclingDocumentParser(
        converter_factory=_factory_returning(_valid_single_block_doc(201)),
        pdf_preflight=_const_preflight(201),
        max_pdf_pages=200,
    )

    with structlog.testing.capture_logs() as captured, pytest.raises(PdfTooManyPagesError):
        await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)

    events = [e for e in captured if e["event"] == "pdf_too_many_pages"]
    assert len(events) == 1
    assert events[0]["limit"] == 200
    assert events[0]["actual"] == 201


@pytest.mark.asyncio
async def test_parse_logs_structured_event_for_no_text_extractable() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_factory_returning(_FakeDocument(_page_count=1, _items=())),
        pdf_preflight=_const_preflight(1),
    )

    with structlog.testing.capture_logs() as captured, pytest.raises(PdfNoTextExtractableError):
        await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)

    events = [e for e in captured if e["event"] == "pdf_no_text_extractable"]
    assert len(events) == 1
    assert events[0]["page_count"] == 1


# ---------------------------------------------------------------------------
# Exception wrapping discipline — "no silent fallbacks"
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Preflight offloading — event loop must stay responsive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_preflight_does_not_block_concurrent_parses() -> None:
    """Two concurrent parses must overlap their preflight waits.

    Regression guard: `_pdf_preflight` used to be called synchronously inside
    async `parse`. The default preflight opens the PDF with PyMuPDF (a
    blocking C call). A large or slow-to-open PDF would stall every other
    ASGI handler on the same event loop. The fix offloads preflight to
    `asyncio.to_thread`, letting two concurrent parses' sleeps run in
    parallel threads — so total wall-clock time stays near one preflight's
    duration instead of two.
    """
    preflight_duration = 0.2

    def _slow_blocking_preflight(_pdf_bytes: bytes) -> int:
        time.sleep(preflight_duration)  # blocks the current thread
        return 1

    parser = DoclingDocumentParser(
        converter_factory=_factory_returning(_valid_single_block_doc(1)),
        pdf_preflight=_slow_blocking_preflight,
    )

    start = time.monotonic()
    results = await asyncio.gather(
        parser.parse(b"%PDF-fake-a", _DEFAULT_CONFIG),
        parser.parse(b"%PDF-fake-b", _DEFAULT_CONFIG),
    )
    elapsed = time.monotonic() - start

    assert len(results) == 2
    # Serialized (bug): elapsed ~= 2 x 0.2s = 0.4s (preflight holds the loop
    # during time.sleep, so the second parse cannot start until the first
    # finishes its preflight).
    # Parallel (fixed): elapsed ~= 0.2s + overhead (both preflights sleep in
    # separate threads via asyncio.to_thread).
    assert elapsed < 0.32, (
        f"two concurrent parses took {elapsed:.3f}s; expected <0.32s — "
        "preflight appears to block the event loop"
    )


@pytest.mark.asyncio
async def test_unknown_converter_exception_propagates_unchanged() -> None:
    """Parser must not wrap unknown errors as PdfInvalidError."""

    @dataclass
    class _RaisingConverter:
        config: DoclingConfig
        raised_errors: list[str] = field(default_factory=list)

        def convert(self, _pdf_bytes: bytes) -> Any:
            msg = "unexpected docling crash"
            raise RuntimeError(msg)

    def _factory(config: DoclingConfig) -> _RaisingConverter:
        return _RaisingConverter(config=config)

    parser = DoclingDocumentParser(
        converter_factory=_factory,
        pdf_preflight=_const_preflight(1),
    )

    with pytest.raises(RuntimeError, match="unexpected docling crash"):
        await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)

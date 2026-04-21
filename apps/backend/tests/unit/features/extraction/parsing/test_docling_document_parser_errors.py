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
import sys
import time
import types
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
from app.features.extraction.parsing import docling_document_parser as parser_mod
from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
    _default_pdf_preflight,
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


async def test_parse_allows_page_count_exactly_at_limit() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_factory_returning(_valid_single_block_doc(200)),
        pdf_preflight=_const_preflight(200),
        max_pdf_pages=200,
    )

    result = await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)

    assert result.page_count == 200
    assert len(result.blocks) == 1


async def test_parse_default_max_pdf_pages_is_200() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_factory_returning(_valid_single_block_doc(201)),
        pdf_preflight=_const_preflight(201),
    )

    with pytest.raises(PdfTooManyPagesError) as excinfo:
        await parser.parse(b"%PDF-fake", _DEFAULT_CONFIG)

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"limit": 200, "actual": 201}


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


# ---------------------------------------------------------------------------
# Issue #232: PyMuPDF exception classification must be isinstance-based, not
# string-match against `type(exc).__name__`.
#
# The previous classifier read
#
#     if type(exc).__name__ not in {"FileDataError", "EmptyFileError"} and not
#         isinstance(exc, ValueError):
#         raise
#
# and would therefore FAIL to catch:
#   1. A subclass of `pymupdf.FileDataError` with any name other than the two
#      literal strings (e.g. a future `CorruptFileError`, or a user-landed
#      subclass introduced in a PyMuPDF minor bump).
#   2. A future rename where `pymupdf.FileDataError` is renamed to something
#      like `pymupdf.PdfFileDataError` on a major release.
#
# The correct classification is `isinstance(exc, pymupdf.FileDataError)`,
# which follows PyMuPDF's published exception hierarchy (EmptyFileError is a
# subclass of FileDataError — verified at runtime against pymupdf 1.27.2.2).
# That is robust to both failure modes above.
# ---------------------------------------------------------------------------


@dataclass
class _FakeFitzDocument:
    _page_count: int = 1
    _needs_pass: bool = False
    closed: bool = False

    @property
    def page_count(self) -> int:
        return self._page_count

    @property
    def needs_pass(self) -> bool:
        return self._needs_pass

    def close(self) -> None:
        self.closed = True


def _build_fake_pymupdf_module() -> types.ModuleType:
    """Build a stand-in ``pymupdf`` module shaped like the real one.

    The module exposes ``FileDataError`` / ``EmptyFileError`` exception
    classes (same superclass relationship as pymupdf 1.27.2.2 — EmptyFileError
    subclasses FileDataError) and a placeholder ``open`` that returns a
    default healthy doc. Tests mutate ``mod.open`` in place to configure the
    side effect for the specific scenario, so an exception raised via
    ``mod.FileDataError(...)`` is the same class the fix sees via the
    installed module — no duplicate class definitions across helper calls.
    """
    mod = types.ModuleType("pymupdf")

    class _FakeFileDataError(RuntimeError):
        pass

    class _FakeEmptyFileError(_FakeFileDataError):
        pass

    mod.FileDataError = _FakeFileDataError  # type: ignore[attr-defined]
    mod.EmptyFileError = _FakeEmptyFileError  # type: ignore[attr-defined]

    default_doc = _FakeFitzDocument()

    def _open_default(*, stream: bytes, filetype: str) -> _FakeFitzDocument:
        del stream, filetype
        return default_doc

    mod.open = _open_default  # type: ignore[attr-defined]
    return mod


def _set_open_raises(mod: types.ModuleType, exc: BaseException) -> None:
    def _open_raising(*, stream: bytes, filetype: str) -> _FakeFitzDocument:
        del stream, filetype
        raise exc

    mod.open = _open_raising  # type: ignore[attr-defined]


def _set_open_returns(mod: types.ModuleType, document: _FakeFitzDocument) -> None:
    def _open_returning(*, stream: bytes, filetype: str) -> _FakeFitzDocument:
        del stream, filetype
        return document

    mod.open = _open_returning  # type: ignore[attr-defined]


def _install_fake_pymupdf(
    monkeypatch: pytest.MonkeyPatch,
    fake_mod: types.ModuleType,
) -> None:
    """Inject ``fake_mod`` so ``importlib.import_module("pymupdf")`` returns it."""
    real_import_module = parser_mod.importlib.import_module

    def _importer(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pymupdf":
            return fake_mod
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(parser_mod.importlib, "import_module", _importer)
    monkeypatch.setitem(sys.modules, "pymupdf", fake_mod)


def test_default_preflight_raises_pdf_invalid_on_file_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``pymupdf.FileDataError`` must surface as ``PdfInvalidError``."""
    fake_mod = _build_fake_pymupdf_module()
    _set_open_raises(fake_mod, fake_mod.FileDataError("malformed PDF"))  # type: ignore[attr-defined]
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(PdfInvalidError):
        _default_pdf_preflight(b"not a pdf")


def test_default_preflight_raises_pdf_invalid_on_empty_file_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``pymupdf.EmptyFileError`` (subclass of FileDataError) must surface as PdfInvalidError."""
    fake_mod = _build_fake_pymupdf_module()
    _set_open_raises(fake_mod, fake_mod.EmptyFileError("empty stream"))  # type: ignore[attr-defined]
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(PdfInvalidError):
        _default_pdf_preflight(b"")


def test_default_preflight_raises_pdf_invalid_on_unknown_file_data_error_subclass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future PyMuPDF subclass (e.g. ``CorruptFileError``) of ``FileDataError``.

    Regression guard for issue #232. The old classifier matched by literal
    class name (``type(exc).__name__ in {"FileDataError", "EmptyFileError"}``)
    and would have let this sibling exception fall through as an unclassified
    500. The fix uses ``isinstance(exc, pymupdf.FileDataError)`` which
    correctly captures the whole subtree of the published hierarchy.
    """
    fake_mod = _build_fake_pymupdf_module()
    base_file_data_error = fake_mod.FileDataError  # type: ignore[attr-defined]

    class _FutureCorruptFileError(base_file_data_error):  # type: ignore[misc,valid-type]
        """Simulates a PyMuPDF subclass added in a later release."""

    # Expose the subclass on the module too so the fix can attribute-look it up
    # if desired (not required — isinstance against FileDataError is enough).
    fake_mod.CorruptFileError = _FutureCorruptFileError  # type: ignore[attr-defined]
    _set_open_raises(fake_mod, _FutureCorruptFileError("xref table damaged"))
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(PdfInvalidError):
        _default_pdf_preflight(b"%PDF-1.7 broken xref")


def test_default_preflight_passes_through_unrelated_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``RuntimeError`` that is not a ``FileDataError`` subclass must NOT be wrapped.

    Anything else (MemoryError, OSError, arbitrary RuntimeError) must propagate
    unchanged — "no silent fallbacks" per PDFX-E003-F004 technical constraint.
    The old code's `type(exc).__name__ not in {...}` path lets these through;
    the new isinstance path must continue to let them through.
    """
    fake_mod = _build_fake_pymupdf_module()
    _set_open_raises(fake_mod, RuntimeError("genuinely unexpected libmupdf crash"))
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(RuntimeError, match="genuinely unexpected libmupdf crash"):
        _default_pdf_preflight(b"%PDF-fake")


def test_default_preflight_passes_through_memory_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resource exhaustion must not be reclassified as PdfInvalidError."""
    fake_mod = _build_fake_pymupdf_module()
    _set_open_raises(fake_mod, MemoryError("OOM"))
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(MemoryError, match="OOM"):
        _default_pdf_preflight(b"%PDF-fake")


def test_default_preflight_propagates_value_error_from_open_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyMuPDF raises ``ValueError`` for argument-shape bugs (e.g. ``stream=str``
    instead of ``bytes``). Those are programmer errors in the calling code,
    not malformed PDF bytes, so the preflight must propagate the ``ValueError``
    unchanged rather than wrapping it as ``PdfInvalidError``. At the API
    boundary, an unhandled ``ValueError`` is caught by the generic exception
    handler and surfaces as ``INTERNAL_ERROR`` (500) — it is not a
    ``DomainError``, so it does not flow through the DomainError handler chain.

    Regression guard for #278.
    """
    fake_mod = _build_fake_pymupdf_module()
    _set_open_raises(fake_mod, ValueError("bad stream"))
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(ValueError, match="bad stream"):
        _default_pdf_preflight(b"?")


def test_default_preflight_detects_password_protected_via_needs_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``doc.needs_pass`` probe must raise ``PdfPasswordProtectedError``.

    The probe is load-bearing: it's the officially documented PyMuPDF way to
    detect encrypted PDFs on a successfully-opened document. No string match
    against exception messages required.
    """
    encrypted_doc = _FakeFitzDocument(_page_count=3, _needs_pass=True)
    fake_mod = _build_fake_pymupdf_module()
    _set_open_returns(fake_mod, encrypted_doc)
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(PdfPasswordProtectedError):
        _default_pdf_preflight(b"%PDF-encrypted")

    # The doc must still be closed even when the preflight raises.
    assert encrypted_doc.closed is True


def test_default_preflight_returns_page_count_on_healthy_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: valid, unencrypted PDF returns its page count."""
    doc = _FakeFitzDocument(_page_count=42, _needs_pass=False)
    fake_mod = _build_fake_pymupdf_module()
    _set_open_returns(fake_mod, doc)
    _install_fake_pymupdf(monkeypatch, fake_mod)

    result = _default_pdf_preflight(b"%PDF-fake-ok")

    assert result == 42
    assert doc.closed is True


def test_default_preflight_logs_structured_event_on_pdf_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``pdf_invalid`` structlog event must be emitted with the exception class name
    (structured key=value, no f-strings in log messages)."""
    fake_mod = _build_fake_pymupdf_module()
    base_error = fake_mod.FileDataError  # type: ignore[attr-defined]

    class _OddlyNamedError(base_error):  # type: ignore[misc,valid-type]
        pass

    _set_open_raises(fake_mod, _OddlyNamedError("xref"))
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with structlog.testing.capture_logs() as captured, pytest.raises(PdfInvalidError):
        _default_pdf_preflight(b"%PDF-broken")

    pdf_invalid_events = [e for e in captured if e["event"] == "pdf_invalid"]
    assert len(pdf_invalid_events) == 1
    assert pdf_invalid_events[0]["reason"] == "_OddlyNamedError"


def test_default_preflight_logs_structured_event_on_password_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``pdf_password_protected`` structlog event must be emitted when the
    ``needs_pass`` probe fires."""
    encrypted_doc = _FakeFitzDocument(_page_count=1, _needs_pass=True)
    fake_mod = _build_fake_pymupdf_module()
    _set_open_returns(fake_mod, encrypted_doc)
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with structlog.testing.capture_logs() as captured, pytest.raises(PdfPasswordProtectedError):
        _default_pdf_preflight(b"%PDF-encrypted")

    password_events = [e for e in captured if e["event"] == "pdf_password_protected"]
    assert len(password_events) == 1


# ---------------------------------------------------------------------------
# PR #245 review + issue #278 follow-up: resolution of ``FileDataError``
# attribute must not degrade into ``RuntimeError`` when the attribute is
# missing or not an exception type.
#
# If a future PyMuPDF release renames ``FileDataError``, the preflight must
# NOT fall back to wrapping every ``RuntimeError`` from ``pymupdf.open`` as
# ``PdfInvalidError`` — that would silently reclassify genuine 500s as 400s,
# violating the "no silent fallbacks" constraint.
#
# Under issue #278, the degraded case now has an EMPTY allow-list:
# ``ValueError`` (and every other exception from ``pymupdf.open``)
# propagates unchanged rather than being wrapped as ``PdfInvalidError``.
# Previous versions of this section said "only ``ValueError`` remains as
# the safe, narrow wrap" — that contract was retired in #278; the
# tests below still pin the "``RuntimeError`` must not wrap as 400"
# invariant, and the ``ValueError``-propagation tests further up lock
# in the new zero-wrap contract.
# ---------------------------------------------------------------------------


def _build_fake_pymupdf_module_without_file_data_error() -> types.ModuleType:
    """Build a fake pymupdf module that omits ``FileDataError`` entirely.

    Simulates a hypothetical future PyMuPDF release that renames or removes
    the ``FileDataError`` symbol.
    """
    mod = types.ModuleType("pymupdf")
    default_doc = _FakeFitzDocument()

    def _open_default(*, stream: bytes, filetype: str) -> _FakeFitzDocument:
        del stream, filetype
        return default_doc

    mod.open = _open_default  # type: ignore[attr-defined]
    return mod


def test_default_preflight_does_not_wrap_runtime_error_when_file_data_error_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``pymupdf`` lacks ``FileDataError`` (API drift), a plain ``RuntimeError``
    from ``pymupdf.open`` must propagate unchanged — NOT be wrapped as
    ``PdfInvalidError``.

    Regression guard for PR #245 review feedback: the previous resolver used
    ``getattr(pymupdf, "FileDataError", RuntimeError)``, which meant a missing
    attribute collapsed classification onto the base ``RuntimeError``, so any
    runtime error from ``pymupdf.open`` (orphan state, transient crashes, …)
    would be reclassified as a 400 ``PdfInvalidError``. That is a silent
    fallback and violates the PDFX-E003-F004 "no silent fallbacks" constraint.
    """
    fake_mod = _build_fake_pymupdf_module_without_file_data_error()
    _set_open_raises(fake_mod, RuntimeError("orphaned object"))
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(RuntimeError, match="orphaned object"):
        _default_pdf_preflight(b"%PDF-fake")


def test_default_preflight_propagates_value_error_when_file_data_error_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when ``FileDataError`` is missing, ``ValueError`` from ``pymupdf.open``
    must propagate unchanged rather than being reclassified as
    ``PdfInvalidError``.

    ``ValueError`` is a programmer-error signal (wrong argument shape); the
    caller has a bug, not the PDF bytes. Reclassifying it as 400 would hide
    real bugs behind a user-facing "malformed PDF" response. At the API
    boundary, an unhandled ``ValueError`` is caught by the generic exception
    handler and surfaces as ``INTERNAL_ERROR`` (500) — it is not a
    ``DomainError``, so it does not flow through the DomainError handler chain.
    See #278.
    """
    fake_mod = _build_fake_pymupdf_module_without_file_data_error()
    _set_open_raises(fake_mod, ValueError("bad stream"))
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(ValueError, match="bad stream"):
        _default_pdf_preflight(b"?")


def test_default_preflight_ignores_non_type_file_data_error_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``pymupdf.FileDataError`` exists but is NOT a ``BaseException`` subclass
    (e.g. a stray non-exception symbol after a refactor), it must be ignored —
    a plain ``RuntimeError`` must still propagate unchanged.
    """
    fake_mod = _build_fake_pymupdf_module_without_file_data_error()
    fake_mod.FileDataError = "not an exception class"  # type: ignore[attr-defined]
    _set_open_raises(fake_mod, RuntimeError("unexpected"))
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(RuntimeError, match="unexpected"):
        _default_pdf_preflight(b"%PDF-fake")


def test_default_preflight_ignores_non_exception_class_file_data_error_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pathological case: ``FileDataError`` is a ``type`` but not a
    ``BaseException`` subclass. It must be ignored so plain ``RuntimeError``
    does not get wrapped as 400.
    """
    fake_mod = _build_fake_pymupdf_module_without_file_data_error()

    class _NotAnException:  # pragma: no cover - placeholder class
        pass

    fake_mod.FileDataError = _NotAnException  # type: ignore[attr-defined]
    _set_open_raises(fake_mod, RuntimeError("unexpected"))
    _install_fake_pymupdf(monkeypatch, fake_mod)

    with pytest.raises(RuntimeError, match="unexpected"):
        _default_pdf_preflight(b"%PDF-fake")

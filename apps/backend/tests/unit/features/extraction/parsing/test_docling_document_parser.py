"""Unit tests for DoclingDocumentParser (PDFX-E003-F002).

All tests drive the parser via a fake `converter_factory` so no real Docling
code path is ever exercised — the tests run offline, deterministically, and
with no Docling dependency installed. Integration tests against the real
Docling pipeline live under the slow marker in
`tests/integration/features/extraction/parsing/`.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.features.extraction.parsing import docling_document_parser as parser_mod
from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
    _default_converter_factory,
)
from app.features.extraction.parsing.document_parser import DocumentParser
from app.features.extraction.parsing.parsed_document import ParsedDocument


def _noop_preflight(_pdf_bytes: bytes) -> int:
    """Preflight stub that accepts any bytes and reports 1 page. Keeps unit tests offline."""
    return 1


# ---------------------------------------------------------------------------
# Fake Docling shapes (implement the parser's local Protocols)
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
class _FakeDoclingDocument:
    _items: tuple[_FakeTextItem, ...]
    _page_count: int

    @property
    def page_count(self) -> int:
        return self._page_count

    def iter_text_items(self) -> list[_FakeTextItem]:
        return list(self._items)


class _RecordingFakeConverter:
    """Fake converter that records convert() calls and the config passed to its factory."""

    def __init__(self, document: _FakeDoclingDocument, *, config: DoclingConfig) -> None:
        self.document = document
        self.config = config
        self.convert_calls: list[bytes] = []

    def convert(self, pdf_bytes: bytes) -> _FakeDoclingDocument:
        self.convert_calls.append(pdf_bytes)
        return self.document


def _make_factory(
    document: _FakeDoclingDocument,
    *,
    captured: list[_RecordingFakeConverter] | None = None,
) -> Any:
    def factory(config: DoclingConfig) -> _RecordingFakeConverter:
        converter = _RecordingFakeConverter(document, config=config)
        if captured is not None:
            captured.append(converter)
        return converter

    return factory


def _two_page_document() -> _FakeDoclingDocument:
    return _FakeDoclingDocument(
        _items=(
            _FakeTextItem(
                text="hello",
                page_number=1,
                bbox_x0=10.0,
                bbox_y0=700.0,
                bbox_x1=80.0,
                bbox_y1=720.0,
            ),
            _FakeTextItem(
                text="world",
                page_number=1,
                bbox_x0=10.0,
                bbox_y0=680.0,
                bbox_x1=90.0,
                bbox_y1=700.0,
            ),
            _FakeTextItem(
                text="page two",
                page_number=2,
                bbox_x0=10.0,
                bbox_y0=710.0,
                bbox_x1=120.0,
                bbox_y1=730.0,
            ),
        ),
        _page_count=2,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_parser_satisfies_document_parser_protocol() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_make_factory(_two_page_document()), pdf_preflight=_noop_preflight
    )

    assert isinstance(parser, DocumentParser)


# ---------------------------------------------------------------------------
# Core parse behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_returns_parsed_document_with_page_count_and_nonempty_blocks() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_make_factory(_two_page_document()), pdf_preflight=_noop_preflight
    )

    result = await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))

    assert isinstance(result, ParsedDocument)
    assert result.page_count == 2
    assert len(result.blocks) > 0


@pytest.mark.asyncio
async def test_every_block_has_valid_page_text_bbox_and_unique_id() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_make_factory(_two_page_document()), pdf_preflight=_noop_preflight
    )

    result = await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))

    assert all(block.page_number in (1, 2) for block in result.blocks)
    assert all(block.text for block in result.blocks)
    for block in result.blocks:
        assert block.bbox.x0 < block.bbox.x1
        assert block.bbox.y0 < block.bbox.y1
    block_ids = [block.block_id for block in result.blocks]
    assert len(set(block_ids)) == len(block_ids)


@pytest.mark.asyncio
async def test_block_ids_follow_p_page_b_index_format() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_make_factory(_two_page_document()), pdf_preflight=_noop_preflight
    )

    result = await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))

    expected = {"p1_b0", "p1_b1", "p2_b0"}
    assert {block.block_id for block in result.blocks} == expected


@pytest.mark.asyncio
async def test_reading_order_is_preserved_from_iter_text_items() -> None:
    parser = DoclingDocumentParser(
        converter_factory=_make_factory(_two_page_document()), pdf_preflight=_noop_preflight
    )

    result = await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))

    assert [block.text for block in result.blocks] == ["hello", "world", "page two"]


@pytest.mark.asyncio
async def test_bounding_boxes_are_passed_through_without_coordinate_flip() -> None:
    """Adapter contract: items expose bottom-left-origin coords, parser trusts them."""
    parser = DoclingDocumentParser(
        converter_factory=_make_factory(_two_page_document()), pdf_preflight=_noop_preflight
    )

    result = await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))

    first = result.blocks[0]
    assert first.bbox.x0 == 10.0
    assert first.bbox.y0 == 700.0
    assert first.bbox.x1 == 80.0
    assert first.bbox.y1 == 720.0


# ---------------------------------------------------------------------------
# Config pass-through and statelessness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docling_config_is_passed_to_converter_factory() -> None:
    captured: list[_RecordingFakeConverter] = []
    parser = DoclingDocumentParser(
        converter_factory=_make_factory(_two_page_document(), captured=captured),
        pdf_preflight=_noop_preflight,
    )
    config = DoclingConfig(ocr="auto", table_mode="fast")

    await parser.parse(b"%PDF-fake", config)

    assert len(captured) == 1
    assert captured[0].config is config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ocr", "table_mode"),
    [("auto", "fast"), ("force", "accurate"), ("off", "fast")],
)
async def test_distinct_configs_produce_distinct_factory_invocations(
    ocr: str,
    table_mode: str,
) -> None:
    captured: list[_RecordingFakeConverter] = []
    parser = DoclingDocumentParser(
        converter_factory=_make_factory(_two_page_document(), captured=captured),
        pdf_preflight=_noop_preflight,
    )
    config = DoclingConfig(ocr=ocr, table_mode=table_mode)

    await parser.parse(b"%PDF-fake", config)

    assert captured[0].config.ocr == ocr
    assert captured[0].config.table_mode == table_mode


@pytest.mark.asyncio
async def test_parser_is_stateless_across_sequential_calls() -> None:
    first_doc = _FakeDoclingDocument(
        _items=(
            _FakeTextItem(
                text="alpha",
                page_number=1,
                bbox_x0=0.0,
                bbox_y0=0.0,
                bbox_x1=5.0,
                bbox_y1=5.0,
            ),
        ),
        _page_count=1,
    )
    second_doc = _FakeDoclingDocument(
        _items=(
            _FakeTextItem(
                text="beta",
                page_number=1,
                bbox_x0=0.0,
                bbox_y0=0.0,
                bbox_x1=5.0,
                bbox_y1=5.0,
            ),
            _FakeTextItem(
                text="gamma",
                page_number=2,
                bbox_x0=0.0,
                bbox_y0=0.0,
                bbox_x1=5.0,
                bbox_y1=5.0,
            ),
        ),
        _page_count=2,
    )
    docs = iter([first_doc, second_doc])

    def factory(_config: DoclingConfig) -> _RecordingFakeConverter:
        return _RecordingFakeConverter(next(docs), config=_config)

    parser = DoclingDocumentParser(converter_factory=factory, pdf_preflight=_noop_preflight)

    result_one = await parser.parse(b"%PDF-fake-1", DoclingConfig(ocr="auto", table_mode="fast"))
    result_two = await parser.parse(b"%PDF-fake-2", DoclingConfig(ocr="auto", table_mode="fast"))

    assert [b.text for b in result_one.blocks] == ["alpha"]
    assert [b.text for b in result_two.blocks] == ["beta", "gamma"]
    assert result_one.page_count == 1
    assert result_two.page_count == 2


# ---------------------------------------------------------------------------
# Event loop non-blocking (asyncio.to_thread offloading)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_does_not_block_event_loop_during_synchronous_convert() -> None:
    """If `asyncio.to_thread` is used, a parallel coroutine must make progress."""

    class _SleepyConverter:
        def __init__(self, document: _FakeDoclingDocument, *, config: DoclingConfig) -> None:
            self.document = document
            self.config = config

        def convert(self, _pdf_bytes: bytes) -> _FakeDoclingDocument:
            time.sleep(0.2)
            return self.document

    def factory(config: DoclingConfig) -> _SleepyConverter:
        return _SleepyConverter(_two_page_document(), config=config)

    parser = DoclingDocumentParser(converter_factory=factory, pdf_preflight=_noop_preflight)

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while ticks < 500:  # upper guard so the test can never run forever
            await asyncio.sleep(0.01)
            ticks += 1

    ticker_task = asyncio.create_task(_ticker())
    await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))
    ticker_task.cancel()
    with contextlib_suppress_cancelled():
        await ticker_task

    # 200ms of blocking convert offloaded to a thread should leave room for
    # the ticker to fire at least ~10 times. We assert 5 as a generous floor
    # to avoid flakes on slow CI while still catching a regression that
    # would put the sleep on the event loop (which would leave ticks == 0).
    assert ticks >= 5


@pytest.mark.asyncio
async def test_parse_does_not_block_event_loop_during_converter_factory() -> None:
    """The converter factory (lazy Docling import + pipeline construction) is
    CPU-bound on cold start. It must be offloaded to a worker thread alongside
    the convert call so the event loop stays responsive.

    This test isolates the factory path: the factory sleeps for 200ms (simulating
    a heavy cold-start import), while convert() itself is instant. If the factory
    runs on the event loop thread, the ticker cannot advance.
    """

    class _InstantConverter:
        def __init__(self, document: _FakeDoclingDocument, *, config: DoclingConfig) -> None:
            self.document = document
            self.config = config

        def convert(self, _pdf_bytes: bytes) -> _FakeDoclingDocument:
            return self.document

    def slow_factory(config: DoclingConfig) -> _InstantConverter:
        time.sleep(0.2)  # simulates heavy lazy-import + pipeline construction
        return _InstantConverter(_two_page_document(), config=config)

    parser = DoclingDocumentParser(converter_factory=slow_factory, pdf_preflight=_noop_preflight)

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while ticks < 500:  # upper guard so the test can never run forever
            await asyncio.sleep(0.01)
            ticks += 1

    ticker_task = asyncio.create_task(_ticker())
    await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))
    ticker_task.cancel()
    with contextlib_suppress_cancelled():
        await ticker_task

    # 200ms of blocking factory offloaded to a thread should leave room for
    # the ticker to fire at least ~10 times. We assert 5 as a generous floor
    # to avoid flakes on slow CI while still catching a regression that
    # would run the factory on the event loop (which would leave ticks == 0).
    assert ticks >= 5, (
        f"ticker did not advance enough (ticks={ticks}); "
        "converter factory appears to block the event loop"
    )


def contextlib_suppress_cancelled() -> Any:
    import contextlib

    return contextlib.suppress(asyncio.CancelledError)


# ---------------------------------------------------------------------------
# Docling logger is configured at module import time
# ---------------------------------------------------------------------------


def test_docling_logger_level_is_raised_at_module_import() -> None:
    """Module-level side effect: `logging.getLogger("docling").setLevel(WARNING)`.

    The parser does not redirect stdout/stderr — that would mutate
    process-global file objects and interleave under concurrent requests.
    Instead, we cap Docling's own logger at WARNING so its INFO/DEBUG stream
    stays out of the service's log bus. Raw `print()` calls from Docling
    are out of scope (they would indicate a bug in Docling itself).
    """
    import logging as _logging

    assert _logging.getLogger("docling").level >= _logging.WARNING


# ---------------------------------------------------------------------------
# Error-passthrough (out-of-scope error codes must not be raised here)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parser_does_not_wrap_converter_errors_as_domain_pdf_errors() -> None:
    """PDFX-E003-F004 owns PDF_* error codes; this parser must not preempt it."""

    class _BrokenConverter:
        def __init__(self, *, config: DoclingConfig) -> None:
            self.config = config

        def convert(self, _pdf_bytes: bytes) -> Any:
            msg = "simulated Docling failure"
            raise RuntimeError(msg)

    def factory(config: DoclingConfig) -> _BrokenConverter:
        return _BrokenConverter(config=config)

    parser = DoclingDocumentParser(converter_factory=factory, pdf_preflight=_noop_preflight)

    with pytest.raises(RuntimeError, match="simulated Docling failure"):
        await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))


# ---------------------------------------------------------------------------
# Default converter factory: structurally distinct pipelines per OCR mode
# ---------------------------------------------------------------------------


class _FakePipelineOptions:
    def __init__(self) -> None:
        self.do_ocr: bool = False
        self.do_table_structure: bool = False
        self.table_structure_options: Any = None
        self.ocr_options: Any = None


class _FakeTableStructureOptions:
    def __init__(self, *, do_cell_matching: bool, mode: Any) -> None:
        self.do_cell_matching = do_cell_matching
        self.mode = mode


class _FakeEasyOcrOptions:
    def __init__(self, *, force_full_page_ocr: bool) -> None:
        self.force_full_page_ocr = force_full_page_ocr


class _FakeTableFormerMode:
    FAST = "FAST"
    ACCURATE = "ACCURATE"


class _FakeInputFormat:
    PDF = "PDF"


class _FakePdfFormatOption:
    def __init__(self, *, pipeline_options: Any) -> None:
        self.pipeline_options = pipeline_options


class _FakeRealDocumentConverter:
    last_format_options: Any = None

    def __init__(self, *, format_options: Any) -> None:
        _FakeRealDocumentConverter.last_format_options = format_options
        self.format_options = format_options

    def convert(self, _source: Any) -> Any:  # pragma: no cover - not exercised here
        class _Result:
            document = None

        return _Result()


def _install_fake_docling_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    base_models_mod = type(sys)("docling.datamodel.base_models")
    base_models_mod.InputFormat = _FakeInputFormat  # type: ignore[attr-defined]
    base_models_mod.DocumentStream = object  # type: ignore[attr-defined]

    pipeline_options_mod = type(sys)("docling.datamodel.pipeline_options")
    pipeline_options_mod.PdfPipelineOptions = _FakePipelineOptions  # type: ignore[attr-defined]
    pipeline_options_mod.TableStructureOptions = _FakeTableStructureOptions  # type: ignore[attr-defined]
    pipeline_options_mod.TableFormerMode = _FakeTableFormerMode  # type: ignore[attr-defined]
    pipeline_options_mod.EasyOcrOptions = _FakeEasyOcrOptions  # type: ignore[attr-defined]

    document_converter_mod = type(sys)("docling.document_converter")
    document_converter_mod.DocumentConverter = _FakeRealDocumentConverter  # type: ignore[attr-defined]
    document_converter_mod.PdfFormatOption = _FakePdfFormatOption  # type: ignore[attr-defined]

    import sys as _sys  # local alias to avoid shadowing

    monkeypatch.setitem(_sys.modules, "docling.datamodel.base_models", base_models_mod)
    monkeypatch.setitem(
        _sys.modules,
        "docling.datamodel.pipeline_options",
        pipeline_options_mod,
    )
    monkeypatch.setitem(_sys.modules, "docling.document_converter", document_converter_mod)


def _extract_pipeline_options() -> _FakePipelineOptions:
    format_options = _FakeRealDocumentConverter.last_format_options
    pdf_format_option = format_options[_FakeInputFormat.PDF]
    return pdf_format_option.pipeline_options  # type: ignore[no-any-return]


def test_default_factory_auto_mode_enables_ocr_without_forcing_full_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_docling_modules(monkeypatch)

    _default_converter_factory(DoclingConfig(ocr="auto", table_mode="fast"))
    pipeline_options = _extract_pipeline_options()

    assert pipeline_options.do_ocr is True
    assert isinstance(pipeline_options.ocr_options, _FakeEasyOcrOptions)
    assert pipeline_options.ocr_options.force_full_page_ocr is False
    assert pipeline_options.table_structure_options.mode == _FakeTableFormerMode.FAST


def test_default_factory_force_mode_sets_force_full_page_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_docling_modules(monkeypatch)

    _default_converter_factory(DoclingConfig(ocr="force", table_mode="accurate"))
    pipeline_options = _extract_pipeline_options()

    assert pipeline_options.do_ocr is True
    assert isinstance(pipeline_options.ocr_options, _FakeEasyOcrOptions)
    assert pipeline_options.ocr_options.force_full_page_ocr is True
    assert pipeline_options.table_structure_options.mode == _FakeTableFormerMode.ACCURATE


def test_default_factory_off_mode_disables_ocr_and_skips_ocr_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_docling_modules(monkeypatch)

    _default_converter_factory(DoclingConfig(ocr="off", table_mode="fast"))
    pipeline_options = _extract_pipeline_options()

    assert pipeline_options.do_ocr is False
    assert pipeline_options.ocr_options is None


def test_default_factory_raises_domain_error_when_docling_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Docling must surface as a DomainError, not a generic RuntimeError.

    Regression guard for issue #153: CLAUDE.md forbids `RuntimeError` inside the
    extraction pipeline. The lazy-import fallback in `_default_converter_factory`
    previously raised `RuntimeError`, which surfaced as an opaque 500 instead of
    a structured `PdfParserUnavailableError` response.
    """
    import importlib as _importlib

    from app.exceptions import PdfParserUnavailableError

    real_import_module = _importlib.import_module

    def failing_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("docling"):
            raise ImportError(name)
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(parser_mod.importlib, "import_module", failing_import)

    with pytest.raises(PdfParserUnavailableError) as excinfo:
        _default_converter_factory(DoclingConfig(ocr="auto", table_mode="fast"))

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"dependency": "docling"}
    # The original ImportError must chain via `raise ... from exc` so operators
    # still see the underlying cause in the traceback.
    assert isinstance(excinfo.value.__cause__, ImportError)


def test_default_pdf_preflight_raises_domain_error_when_pymupdf_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing PyMuPDF must surface as a DomainError, not a generic RuntimeError.

    Regression guard for issue #153: `_default_pdf_preflight` previously raised
    `RuntimeError` on missing pymupdf. Must now raise
    `PdfParserUnavailableError` with the offending dependency name.
    """
    import importlib as _importlib

    from app.exceptions import PdfParserUnavailableError
    from app.features.extraction.parsing.docling_document_parser import (
        _default_pdf_preflight,
    )

    real_import_module = _importlib.import_module

    def failing_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pymupdf":
            raise ImportError(name)
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(parser_mod.importlib, "import_module", failing_import)

    with pytest.raises(PdfParserUnavailableError) as excinfo:
        _default_pdf_preflight(b"%PDF-fake")

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"dependency": "pymupdf"}
    assert isinstance(excinfo.value.__cause__, ImportError)


# ---------------------------------------------------------------------------
# Containment: docling imports live only in docling_document_parser.py
# ---------------------------------------------------------------------------


_EXTRACTION_ROOT = Path(__file__).resolve().parents[5] / "app" / "features" / "extraction"


def _collect_files_referencing_docling() -> set[Path]:
    """Return every .py file under extraction/ whose source references docling.

    Uses an AST walk to catch static imports plus a source-text scan for
    `importlib.import_module("docling...")` style dynamic imports.
    """
    matches: set[Path] = set()
    for path in _EXTRACTION_ROOT.rglob("*.py"):
        source = path.read_text()
        found = False
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(alias.name.split(".")[0] == "docling" for alias in node.names):
                        found = True
                        break
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and node.module.split(".")[0] == "docling"
                ) or (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value.split(".")[0] == "docling"
                ):
                    found = True
                    break
        if found:
            matches.add(path)
    return matches


def test_only_docling_document_parser_references_docling() -> None:
    matches = _collect_files_referencing_docling()
    expected = _EXTRACTION_ROOT / "parsing" / "docling_document_parser.py"

    assert matches == {expected}, (
        f"docling imports must be confined to {expected}, found: {sorted(matches)}"
    )

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
import structlog

from app.features.extraction.parsing import (
    _real_docling_converter_adapter as converter_adapter_mod,
)
from app.features.extraction.parsing import docling_document_parser as parser_mod
from app.features.extraction.parsing._real_docling_converter_adapter import (
    default_converter_factory,
)
from app.features.extraction.parsing._real_docling_document_adapter import (
    RealDoclingDocumentAdapter,
)
from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
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
# Docling logger suppression is driven from configure_logging (issue #210)
# ---------------------------------------------------------------------------


def test_docling_logger_level_is_raised_after_configure_logging() -> None:
    """After `configure_logging()` runs, the docling stdlib logger is capped at WARNING.

    Before issue #210, `docling_document_parser.py` set this at module
    import time via a direct `logging.getLogger("docling").setLevel(WARNING)`
    call. That violated CLAUDE.md's "no `logging.getLogger` outside
    `app/core/logging.py`" rule. The fix moves the call into
    `configure_logging` via the `silence_stdlib_logger` helper, so the
    invariant still holds in production (where `configure_logging` runs in
    `create_app`) but no longer depends on a module-import side effect.

    The parser does not redirect stdout/stderr — that would mutate
    process-global file objects and interleave under concurrent requests.
    Instead, we cap Docling's own logger at WARNING so its INFO/DEBUG stream
    stays out of the service's log bus. Raw `print()` calls from Docling
    are out of scope (they would indicate a bug in Docling itself).
    """
    import logging as _logging

    from app.core.logging import configure_logging

    # Reset to a non-target level first so the assertion observes the side
    # effect of `configure_logging`, not a leftover from a prior test.
    _logging.getLogger("docling").setLevel(_logging.DEBUG)

    configure_logging(log_level="info", json_output=False)

    assert _logging.getLogger("docling").level == _logging.WARNING


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


class _FakeTesseractCliOcrOptions:
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
    pipeline_options_mod.TesseractCliOcrOptions = _FakeTesseractCliOcrOptions  # type: ignore[attr-defined]

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

    default_converter_factory(DoclingConfig(ocr="auto", table_mode="fast"))
    pipeline_options = _extract_pipeline_options()

    assert pipeline_options.do_ocr is True
    assert isinstance(pipeline_options.ocr_options, _FakeTesseractCliOcrOptions)
    assert pipeline_options.ocr_options.force_full_page_ocr is False
    assert pipeline_options.table_structure_options.mode == _FakeTableFormerMode.FAST


def test_default_factory_force_mode_sets_force_full_page_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_docling_modules(monkeypatch)

    default_converter_factory(DoclingConfig(ocr="force", table_mode="accurate"))
    pipeline_options = _extract_pipeline_options()

    assert pipeline_options.do_ocr is True
    assert isinstance(pipeline_options.ocr_options, _FakeTesseractCliOcrOptions)
    assert pipeline_options.ocr_options.force_full_page_ocr is True
    assert pipeline_options.table_structure_options.mode == _FakeTableFormerMode.ACCURATE


def test_default_factory_off_mode_disables_ocr_and_skips_ocr_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_docling_modules(monkeypatch)

    default_converter_factory(DoclingConfig(ocr="off", table_mode="fast"))
    pipeline_options = _extract_pipeline_options()

    assert pipeline_options.do_ocr is False
    assert pipeline_options.ocr_options is None


def test_default_factory_raises_domain_error_when_docling_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Docling must surface as a DomainError, not a generic RuntimeError.

    Regression guard for issue #153: CLAUDE.md forbids `RuntimeError` inside the
    extraction pipeline. The lazy-import fallback in `default_converter_factory`
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

    monkeypatch.setattr(converter_adapter_mod.importlib, "import_module", failing_import)

    with pytest.raises(PdfParserUnavailableError) as excinfo:
        default_converter_factory(DoclingConfig(ocr="auto", table_mode="fast"))

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


def test_only_docling_adapter_files_reference_docling() -> None:
    matches = _collect_files_referencing_docling()
    # Issue #159 split the monolithic parser into multiple single-class files
    # inside the parsing/ package. The Docling containment boundary is now the
    # set of files below; any other file importing `docling` is a regression.
    parsing_dir = _EXTRACTION_ROOT / "parsing"
    expected = {
        parsing_dir / "docling_document_parser.py",
        parsing_dir / "_real_docling_converter_adapter.py",
        parsing_dir / "_real_docling_document_adapter.py",
    }

    # Allow the expected files to reference `docling` but require that no
    # other file does — this is a no-extras guarantee, not a filter. Expected
    # files that don't touch `docling` (e.g. the document adapter only
    # receives `Any`-typed objects, so its source has no `docling.*` string
    # reachable by AST walk) are fine; the invariant is that `matches` has
    # no file outside the expected allow-list.
    extra = matches - expected
    assert extra == set(), (
        f"docling imports must be confined to {sorted(expected)}, extra: {sorted(extra)}"
    )


# ---------------------------------------------------------------------------
# Coordinate-origin normalization in RealDoclingDocumentAdapter
# (regression for GH issue #133)
#
# Docling's `CoordOrigin` defaults to TOPLEFT. The adapter must normalize any
# TOPLEFT bbox to BOTTOMLEFT before yielding a `FlatDoclingTextItem`, because
# downstream `BoundingBox` enforces `y0 <= y1` in a BOTTOMLEFT convention. The
# fakes below mirror the shape of Docling's real types (`prov.bbox.l/.t/.r/.b/
# .coord_origin` with a `to_bottom_left_origin(page_height=...)` method) so the
# adapter exercises the same branches it would against the live library.
# ---------------------------------------------------------------------------


class _FakeDoclingBBox:
    """Mirror of Docling's BoundingBox for adapter-level tests.

    Implements only the attributes the adapter reads (`l/t/r/b/coord_origin`)
    and the `to_bottom_left_origin(page_height=...)` conversion whose semantics
    the adapter must drive. `coord_origin` is a `str` (not an enum) because
    Docling's own `CoordOrigin` subclasses `str, Enum` and the adapter checks
    `str(coord_origin).endswith("TOPLEFT")` to match real enum string values
    like `"CoordOrigin.TOPLEFT"` — so bare strings like `"TOPLEFT"` and
    `"BOTTOMLEFT"` also satisfy the suffix check and work in these fixtures.
    """

    def __init__(
        self,
        *,
        left: float,
        top: float,
        right: float,
        bottom: float,
        coord_origin: str,
    ) -> None:
        self.l = left
        self.t = top
        self.r = right
        self.b = bottom
        self.coord_origin = coord_origin

    def to_bottom_left_origin(self, page_height: float) -> _FakeDoclingBBox:
        if self.coord_origin == "BOTTOMLEFT":
            return _FakeDoclingBBox(
                left=self.l,
                top=self.t,
                right=self.r,
                bottom=self.b,
                coord_origin="BOTTOMLEFT",
            )
        return _FakeDoclingBBox(
            left=self.l,
            top=page_height - self.t,
            right=self.r,
            bottom=page_height - self.b,
            coord_origin="BOTTOMLEFT",
        )


@dataclass
class _FakeDoclingProv:
    page_no: int
    bbox: _FakeDoclingBBox


@dataclass
class _FakeDoclingTextNode:
    text: str
    prov: list[_FakeDoclingProv]


@dataclass
class _FakeDoclingPageSize:
    height: float
    width: float = 600.0


@dataclass
class _FakeDoclingPageItem:
    size: _FakeDoclingPageSize


@dataclass
class _FakeRawDoclingDocument:
    """Fake shape matching what `RealDoclingDocumentAdapter` reads from a live
    DoclingDocument: `.texts` (list of text nodes in STORAGE order), `.pages`
    (dict keyed by page_no to a PageItem carrying `.size.height`), and
    `iterate_items()` which yields `(item, level)` tuples in READING order
    (top-to-bottom, left-to-right, across page breaks) — see Docling 2.88's
    `DoclingDocument.iterate_items` API.

    The default `iterate_items` implementation simply yields the `.texts` in
    order (identity between storage and reading order). Tests that need to
    exercise the reading-order path set `iterate_order` explicitly to a
    permutation of `.texts` that differs from storage order.
    """

    texts: list[_FakeDoclingTextNode]
    pages: dict[int, _FakeDoclingPageItem]
    iterate_order: list[_FakeDoclingTextNode] | None = None

    def iterate_items(self) -> list[tuple[_FakeDoclingTextNode, int]]:
        order = self.iterate_order if self.iterate_order is not None else self.texts
        return [(node, 0) for node in order]


def test_adapter_normalizes_top_left_origin_bbox_to_bottom_left() -> None:
    """Issue #133: a TOPLEFT-origin prov bbox must emerge as BOTTOMLEFT coords.

    Given a 1000-pt-tall page with a TOPLEFT bbox (t=100, b=300), the adapter
    must y-flip against page height so the `FlatDoclingTextItem` reports
    `bbox_y0 < bbox_y1` (900, 700 is invalid — we want 700, 900 in the item).
    """
    raw_doc = _FakeRawDoclingDocument(
        texts=[
            _FakeDoclingTextNode(
                text="hello",
                prov=[
                    _FakeDoclingProv(
                        page_no=1,
                        bbox=_FakeDoclingBBox(
                            left=10.0,
                            top=100.0,
                            right=80.0,
                            bottom=300.0,
                            coord_origin="TOPLEFT",
                        ),
                    ),
                ],
            ),
        ],
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    items = list(adapter.iter_text_items())

    assert len(items) == 1
    only = items[0]
    assert only.text == "hello"
    assert only.page_number == 1
    assert only.bbox_x0 == 10.0
    assert only.bbox_x1 == 80.0
    # y-flip: page_height - top = 900, page_height - bottom = 700.
    # BOTTOMLEFT convention requires y0 (bottom) <= y1 (top), so 700 <= 900.
    assert only.bbox_y0 == 700.0
    assert only.bbox_y1 == 900.0
    assert only.bbox_y0 < only.bbox_y1


def test_adapter_passes_through_bottom_left_origin_bbox_unchanged() -> None:
    """A bbox already in BOTTOMLEFT origin must not be y-flipped again."""
    raw_doc = _FakeRawDoclingDocument(
        texts=[
            _FakeDoclingTextNode(
                text="world",
                prov=[
                    _FakeDoclingProv(
                        page_no=1,
                        bbox=_FakeDoclingBBox(
                            left=10.0,
                            top=720.0,
                            right=90.0,
                            bottom=700.0,
                            coord_origin="BOTTOMLEFT",
                        ),
                    ),
                ],
            ),
        ],
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    items = list(adapter.iter_text_items())

    assert len(items) == 1
    only = items[0]
    assert only.bbox_x0 == 10.0
    assert only.bbox_y0 == 700.0
    assert only.bbox_x1 == 90.0
    assert only.bbox_y1 == 720.0


def test_adapter_normalization_yields_valid_bounding_box_invariant() -> None:
    """End-to-end: a TOPLEFT prov bbox must not trigger the ValueError in
    `BoundingBox.__post_init__` that prompted issue #133.

    This is a regression guard against reintroducing the raw pass-through —
    constructing `BoundingBox(x0=10, y0=900, x1=80, y1=700)` would raise, so
    if the adapter ever forgets to normalize we find out at the outer level
    where it actually hurts the pipeline.
    """
    raw_doc = _FakeRawDoclingDocument(
        texts=[
            _FakeDoclingTextNode(
                text="top-left-page",
                prov=[
                    _FakeDoclingProv(
                        page_no=1,
                        bbox=_FakeDoclingBBox(
                            left=10.0,
                            top=50.0,
                            right=80.0,
                            bottom=200.0,
                            coord_origin="TOPLEFT",
                        ),
                    ),
                ],
            ),
        ],
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    items = list(adapter.iter_text_items())

    assert len(items) == 1
    # Feeding the item's bbox coords into BoundingBox must not raise.
    from app.features.extraction.parsing.bounding_box import BoundingBox

    bbox = BoundingBox(
        x0=items[0].bbox_x0,
        y0=items[0].bbox_y0,
        x1=items[0].bbox_x1,
        y1=items[0].bbox_y1,
    )
    assert bbox.y0 <= bbox.y1
    assert bbox.x0 <= bbox.x1


def test_adapter_raises_key_error_when_prov_page_no_missing_from_pages() -> None:
    """If a text item's prov references a page_no that isn't in `doc.pages`,
    the adapter must surface the programmer/library-contract violation with a
    KeyError whose message names the missing page. Silently falling through
    `pages.get(...)` would yield `None` and blow up with a less actionable
    AttributeError on `page.size.height`.
    """
    raw_doc = _FakeRawDoclingDocument(
        texts=[
            _FakeDoclingTextNode(
                text="orphan-prov",
                prov=[
                    _FakeDoclingProv(
                        page_no=7,
                        bbox=_FakeDoclingBBox(
                            left=10.0,
                            top=100.0,
                            right=80.0,
                            bottom=300.0,
                            coord_origin="TOPLEFT",
                        ),
                    ),
                ],
            ),
        ],
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    with pytest.raises(KeyError) as excinfo:
        list(adapter.iter_text_items())
    assert "7" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Reading-order traversal in RealDoclingDocumentAdapter
# (regression for GH issue #150)
#
# Docling's `document.texts` is an implementation-dependent STORAGE order that
# does not match the visual READING order on multi-column / table-heavy
# layouts. If the adapter iterates `.texts` directly, LangExtract receives a
# concatenated text where columns interleave incorrectly and the span resolver
# reports spurious `hallucinated_offsets` for values that are in fact present.
#
# The adapter must delegate to `DoclingDocument.iterate_items()`, the public
# API that walks the document hierarchy in reading order (top-to-bottom,
# left-to-right per page, respecting column / table structure).
# ---------------------------------------------------------------------------


def _bbox_bottom_left(*, left: float, bottom: float, right: float, top: float) -> _FakeDoclingBBox:
    """Helper: build a BOTTOMLEFT-origin fake bbox with `y0 = bottom <= y1 = top`."""
    return _FakeDoclingBBox(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        coord_origin="BOTTOMLEFT",
    )


def test_iter_text_items_returns_reading_order_for_multi_column_layout() -> None:
    """Issue #150: multi-column pages must emerge in reading order, not storage order.

    Simulate a two-column page where Docling's `.texts` storage lists all of
    column A first ("A-top", "A-bottom"), then all of column B ("B-top",
    "B-bottom"). The visual reading order (which `iterate_items()` is
    contracted to produce) interleaves them: A-top, B-top, A-bottom,
    B-bottom. The adapter must yield the items in that reading order.

    Before the fix, the adapter walked `.texts` and produced storage order
    (A-top, A-bottom, B-top, B-bottom), which downstream `TextConcatenator`
    concatenates into a text blob where LangExtract cannot match cross-
    column values.
    """
    page = _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0, width=600.0))
    a_top = _FakeDoclingTextNode(
        text="A-top",
        prov=[
            _FakeDoclingProv(
                page_no=1,
                bbox=_bbox_bottom_left(left=50.0, bottom=900.0, right=250.0, top=950.0),
            ),
        ],
    )
    a_bottom = _FakeDoclingTextNode(
        text="A-bottom",
        prov=[
            _FakeDoclingProv(
                page_no=1,
                bbox=_bbox_bottom_left(left=50.0, bottom=500.0, right=250.0, top=550.0),
            ),
        ],
    )
    b_top = _FakeDoclingTextNode(
        text="B-top",
        prov=[
            _FakeDoclingProv(
                page_no=1,
                bbox=_bbox_bottom_left(left=350.0, bottom=900.0, right=550.0, top=950.0),
            ),
        ],
    )
    b_bottom = _FakeDoclingTextNode(
        text="B-bottom",
        prov=[
            _FakeDoclingProv(
                page_no=1,
                bbox=_bbox_bottom_left(left=350.0, bottom=500.0, right=550.0, top=550.0),
            ),
        ],
    )
    raw_doc = _FakeRawDoclingDocument(
        # `.texts` carries STORAGE order (column A first, then column B).
        texts=[a_top, a_bottom, b_top, b_bottom],
        pages={1: page},
        # `iterate_items()` carries READING order (interleaved across columns).
        iterate_order=[a_top, b_top, a_bottom, b_bottom],
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    items = list(adapter.iter_text_items())

    assert [item.text for item in items] == ["A-top", "B-top", "A-bottom", "B-bottom"]


def test_iter_text_items_uses_iterate_items_and_ignores_texts_order() -> None:
    """Adapter contract: if `iterate_items()` is present, `.texts` is unused for ordering.

    This pins the implementation to the reading-order API. Placing `.texts`
    in a deliberately different order from `iterate_items()` ensures a
    regression that reverts to the old `.texts` walk fails this test.
    """
    node_one = _FakeDoclingTextNode(
        text="first-in-reading-order",
        prov=[
            _FakeDoclingProv(
                page_no=1,
                bbox=_bbox_bottom_left(left=10.0, bottom=800.0, right=100.0, top=820.0),
            ),
        ],
    )
    node_two = _FakeDoclingTextNode(
        text="second-in-reading-order",
        prov=[
            _FakeDoclingProv(
                page_no=1,
                bbox=_bbox_bottom_left(left=10.0, bottom=700.0, right=100.0, top=720.0),
            ),
        ],
    )
    raw_doc = _FakeRawDoclingDocument(
        # Reverse storage order — if the adapter reads this, the assertion fails.
        texts=[node_two, node_one],
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
        iterate_order=[node_one, node_two],
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    items = list(adapter.iter_text_items())

    assert [item.text for item in items] == [
        "first-in-reading-order",
        "second-in-reading-order",
    ]


def test_iter_text_items_falls_back_to_texts_when_iterate_items_missing() -> None:
    """Graceful fallback: if a DoclingDocument lacks `iterate_items` (older
    Docling build or a test double without the method), the adapter must
    still produce something rather than crash. Storage order is the only
    available signal in that case, so it is acceptable as a fallback.
    """

    @dataclass
    class _LegacyDoclingDocument:
        texts: list[_FakeDoclingTextNode]
        pages: dict[int, _FakeDoclingPageItem]

    node = _FakeDoclingTextNode(
        text="legacy",
        prov=[
            _FakeDoclingProv(
                page_no=1,
                bbox=_bbox_bottom_left(left=10.0, bottom=700.0, right=100.0, top=720.0),
            ),
        ],
    )
    raw_doc = _LegacyDoclingDocument(
        texts=[node],
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    items = list(adapter.iter_text_items())

    assert [item.text for item in items] == ["legacy"]


# ---------------------------------------------------------------------------
# Reading-order fallback: surface shape drift instead of silently truncating
# (regression for GH issue #341)
#
# The `_iter_reading_order` fallback used to do
# `yield from getattr(self._docling_document, "texts", None) or []`, which
# silently yielded nothing whenever `.texts` was missing OR non-iterable
# (e.g., an int, a string, a method object). A Docling version change that
# renamed `.texts` or mutated its shape would then produce empty reading-
# order output with no error and no log — "Docling shape broken" would
# surface downstream as the misleading `PdfNoTextExtractableError`. The
# adapter must raise a structured `PdfParserUnavailableError` instead so
# operators see the drift at the true failure site.
# ---------------------------------------------------------------------------


def test_iter_text_items_raises_when_iterate_items_missing_and_texts_absent() -> None:
    """Issue #341: if neither `iterate_items` nor `.texts` is available, the
    adapter must raise `PdfParserUnavailableError(dependency='docling')`
    instead of silently yielding zero items and letting the parser
    misattribute the failure to the PDF.
    """
    from app.exceptions import PdfParserUnavailableError

    @dataclass
    class _ShapeBrokenDoclingDocument:
        # No `iterate_items`, no `.texts` — simulates a future Docling shape
        # change that the adapter does not understand.
        pages: dict[int, _FakeDoclingPageItem]

    raw_doc = _ShapeBrokenDoclingDocument(
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    with pytest.raises(PdfParserUnavailableError) as excinfo:
        list(adapter.iter_text_items())

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"dependency": "docling"}


def test_iter_text_items_raises_when_iterate_items_missing_and_texts_non_iterable() -> None:
    """Issue #341: a `.texts` attribute whose value is not iterable (e.g., an
    integer, a method object, or any other scalar from a Docling shape
    change) must surface as `PdfParserUnavailableError` instead of silently
    truncating the reading-order stream.
    """
    from app.exceptions import PdfParserUnavailableError

    @dataclass
    class _NonIterableTextsDoclingDocument:
        # `.texts` is a scalar — cannot be iterated. Mimics a Docling rename
        # that turns `.texts` into a count/length or a method accessor.
        texts: int
        pages: dict[int, _FakeDoclingPageItem]

    raw_doc = _NonIterableTextsDoclingDocument(
        texts=42,
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    with pytest.raises(PdfParserUnavailableError) as excinfo:
        list(adapter.iter_text_items())

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"dependency": "docling"}


def test_iter_text_items_fallback_accepts_empty_iterable_texts() -> None:
    """Issue #341 boundary: the "surface shape drift" guard must NOT misfire
    on a legitimately empty `.texts` sequence. An empty list/tuple is a
    valid shape — the document simply has no text items — and must return
    no items rather than raising.
    """

    @dataclass
    class _EmptyLegacyDoclingDocument:
        texts: list[_FakeDoclingTextNode]
        pages: dict[int, _FakeDoclingPageItem]

    raw_doc = _EmptyLegacyDoclingDocument(
        texts=[],
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    items = list(adapter.iter_text_items())

    assert items == []


def test_iter_text_items_raises_when_texts_is_string() -> None:
    """Issue #341 (Copilot follow-up): a `.texts` attribute whose value is a
    `str` must surface as `PdfParserUnavailableError`. `str` is technically
    iterable (has `__iter__`), so a naive `hasattr(texts, "__iter__")` guard
    would accept it and then `yield from "..."` would yield characters —
    every character has no `.text`/`.prov` so the adapter silently produces
    zero items, exactly the "empty output with no error" failure mode this
    guard exists to prevent. Explicitly rejected even though `iter(str)`
    succeeds.
    """
    from app.exceptions import PdfParserUnavailableError

    @dataclass
    class _StringTextsDoclingDocument:
        # `.texts` is a string sentinel (e.g., a "lazy placeholder" from a
        # Docling shape change). Must raise, not yield characters.
        texts: str
        pages: dict[int, _FakeDoclingPageItem]

    raw_doc = _StringTextsDoclingDocument(
        texts="some-unexpected-string",
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    with pytest.raises(PdfParserUnavailableError) as excinfo:
        list(adapter.iter_text_items())

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"dependency": "docling"}


def test_iter_text_items_raises_when_texts_is_bytes() -> None:
    """Issue #341 (Copilot follow-up): same as the `str` case for `bytes` /
    `bytearray`. Iterating bytes yields integer byte values — also silently
    drops to zero items after the `.text`/`.prov` filter. Rejected
    explicitly.
    """
    from app.exceptions import PdfParserUnavailableError

    @dataclass
    class _BytesTextsDoclingDocument:
        texts: bytes
        pages: dict[int, _FakeDoclingPageItem]

    raw_doc = _BytesTextsDoclingDocument(
        texts=b"some-unexpected-bytes",
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    with pytest.raises(PdfParserUnavailableError) as excinfo:
        list(adapter.iter_text_items())

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"dependency": "docling"}


def test_iter_text_items_raises_when_texts_is_mapping() -> None:
    """Issue #341 (Copilot follow-up): a `.texts` attribute whose value is a
    `dict` (or any `Mapping`) must surface as `PdfParserUnavailableError`.
    Iterating a dict yields keys — which have no `.text` / `.prov` — so the
    adapter silently produces zero items. Mappings are rejected explicitly
    to pin this failure mode at the true failure site.
    """
    from app.exceptions import PdfParserUnavailableError

    @dataclass
    class _MappingTextsDoclingDocument:
        texts: dict[str, Any]
        pages: dict[int, _FakeDoclingPageItem]

    raw_doc = _MappingTextsDoclingDocument(
        texts={"unexpected-key": "unexpected-value"},
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    with pytest.raises(PdfParserUnavailableError) as excinfo:
        list(adapter.iter_text_items())

    assert excinfo.value.params is not None
    assert excinfo.value.params.model_dump() == {"dependency": "docling"}


def test_iter_text_items_shape_drift_log_distinguishes_iterate_items_states() -> None:
    """Issue #341 (Copilot follow-up): the `docling_shape_unrecognized` log
    must distinguish "attribute missing" from "attribute present but not
    callable". A non-callable `iterate_items` attribute is a plausible
    shape-drift scenario (Docling renamed the method to a property, say),
    and operators need the log to surface that exactly.

    Fail-safely: the adapter must still raise `PdfParserUnavailableError`
    in that case (non-callable `iterate_items` falls through to the
    `.texts` branch, and an absent `.texts` then triggers the guard).
    """
    from app.exceptions import PdfParserUnavailableError

    @dataclass
    class _NonCallableIterateItemsDocument:
        # `iterate_items` is a non-callable attribute (a string sentinel),
        # and `.texts` is absent — must raise AND the log must record
        # `has_iterate_items_attr=True` with `iterate_items_callable=False`.
        iterate_items: str
        pages: dict[int, _FakeDoclingPageItem]

    raw_doc = _NonCallableIterateItemsDocument(
        iterate_items="not-callable",
        pages={1: _FakeDoclingPageItem(size=_FakeDoclingPageSize(height=1000.0))},
    )
    adapter = RealDoclingDocumentAdapter(raw_doc)

    with structlog.testing.capture_logs() as captured, pytest.raises(PdfParserUnavailableError):
        list(adapter.iter_text_items())

    drift_events = [e for e in captured if e["event"] == "docling_shape_unrecognized"]
    assert len(drift_events) == 1
    event = drift_events[0]
    assert event["has_iterate_items_attr"] is True
    assert event["iterate_items_callable"] is False
    assert event["has_texts"] is False

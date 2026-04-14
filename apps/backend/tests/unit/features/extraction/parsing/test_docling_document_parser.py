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

from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
)
from app.features.extraction.parsing.document_parser import DocumentParser
from app.features.extraction.parsing.parsed_document import ParsedDocument

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
    parser = DoclingDocumentParser(converter_factory=_make_factory(_two_page_document()))

    assert isinstance(parser, DocumentParser)


# ---------------------------------------------------------------------------
# Core parse behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_returns_parsed_document_with_page_count_and_nonempty_blocks() -> None:
    parser = DoclingDocumentParser(converter_factory=_make_factory(_two_page_document()))

    result = await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))

    assert isinstance(result, ParsedDocument)
    assert result.page_count == 2
    assert len(result.blocks) > 0


@pytest.mark.asyncio
async def test_every_block_has_valid_page_text_bbox_and_unique_id() -> None:
    parser = DoclingDocumentParser(converter_factory=_make_factory(_two_page_document()))

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
    parser = DoclingDocumentParser(converter_factory=_make_factory(_two_page_document()))

    result = await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))

    expected = {"p1_b0", "p1_b1", "p2_b0"}
    assert {block.block_id for block in result.blocks} == expected


@pytest.mark.asyncio
async def test_reading_order_is_preserved_from_iter_text_items() -> None:
    parser = DoclingDocumentParser(converter_factory=_make_factory(_two_page_document()))

    result = await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))

    assert [block.text for block in result.blocks] == ["hello", "world", "page two"]


@pytest.mark.asyncio
async def test_bounding_boxes_are_passed_through_without_coordinate_flip() -> None:
    """Adapter contract: items expose bottom-left-origin coords, parser trusts them."""
    parser = DoclingDocumentParser(converter_factory=_make_factory(_two_page_document()))

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

    parser = DoclingDocumentParser(converter_factory=factory)

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

    parser = DoclingDocumentParser(converter_factory=factory)

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


def contextlib_suppress_cancelled() -> Any:
    import contextlib

    return contextlib.suppress(asyncio.CancelledError)


# ---------------------------------------------------------------------------
# stdout/stderr silencing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parser_suppresses_stdout_writes_from_converter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _NoisyConverter:
        def __init__(self, document: _FakeDoclingDocument, *, config: DoclingConfig) -> None:
            self.document = document
            self.config = config

        def convert(self, _pdf_bytes: bytes) -> _FakeDoclingDocument:
            sys.stdout.write("DOCLING NOISE ON STDOUT\n")
            sys.stderr.write("DOCLING NOISE ON STDERR\n")
            return self.document

    def factory(config: DoclingConfig) -> _NoisyConverter:
        return _NoisyConverter(_two_page_document(), config=config)

    parser = DoclingDocumentParser(converter_factory=factory)

    await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))

    captured = capsys.readouterr()
    assert "DOCLING NOISE ON STDOUT" not in captured.out
    assert "DOCLING NOISE ON STDERR" not in captured.err


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

    parser = DoclingDocumentParser(converter_factory=factory)

    with pytest.raises(RuntimeError, match="simulated Docling failure"):
        await parser.parse(b"%PDF-fake", DoclingConfig(ocr="auto", table_mode="fast"))


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

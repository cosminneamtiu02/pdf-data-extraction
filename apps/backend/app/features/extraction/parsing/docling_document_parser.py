"""DoclingDocumentParser: the only file in the repository permitted to use Docling.

This module implements the `DocumentParser` Protocol from PDFX-E003-F001 against
the Docling library. Docling is a heavy AI dependency (ONNX runtime, image
processing, OCR weights) and its import alone is an appreciable chunk of the
service's cold-start budget. Containing `import docling` to this single file
keeps the blast radius small and mirrors the architectural contract that
`import-linter` will enforce in PDFX-E007-F004.

Design summary (load-bearing; do not re-architect without updating the spec):

* The parser is a thin coordinator. It delegates PDF parsing to a Docling
  `DocumentConverter`, walks the resulting Docling document, and emits our own
  feature-owned value types (`ParsedDocument`, `TextBlock`, `BoundingBox`).
* Both the converter factory (which lazy-imports Docling and constructs the
  pipeline) and the conversion step are synchronous and CPU-bound. `async
  parse` offloads them together in a single `asyncio.to_thread` call so the
  FastAPI event loop is never starved during cold start or parsing.
* The concrete Docling objects are obtained through a `converter_factory`
  callable injected via the constructor. The default factory performs a
  *lazy* import of Docling and builds a real `DocumentConverter`; unit tests
  pass a fake factory and therefore never trigger the real import. This is
  why module load stays cheap and why unit tests run without Docling being
  installed locally (Docling becomes a pinned runtime dependency in the
  sibling feature PDFX-E001-F002).
* Everything the parser consumes from the converter is duck-typed against
  small local `_DoclingConverterLike` / `_DoclingDocumentLike` / `_DoclingTextItemLike`
  Protocols. The real-Docling adapter in `_default_converter_factory` bridges
  Docling's public types into these Protocols so that `_to_parsed_document`
  does not need to know anything about Docling's own class hierarchy.
* Coordinate convention: every `BoundingBox` this parser emits is in PDF page
  coordinates with origin bottom-left. The local `_DoclingTextItemLike`
  Protocol requires adapters to expose bottom-left-origin values; the
  real-Docling adapter performs the translation there if Docling's native
  convention disagrees (verified empirically during PDFX-E003-F002 integration
  testing — open question #3 in the feature spec).

Carve-out to CLAUDE.md "one class per file":
This file holds four classes — `DoclingDocumentParser` plus three private
adapters (`_RealDoclingConverterAdapter`, `_RealDoclingDocumentAdapter`,
`_FlatDoclingTextItem`). The adapters cannot live in sibling files because
every line that reads Docling's real types must stay inside this single file
to honor the Docling-containment technical constraint (enforced in PDFX-E007-F004
via `import-linter`). They are module-private (leading underscore), exist only
to bridge Docling's concrete types into this parser's local `_DoclingXxxLike`
Protocols, and are never imported from anywhere else in the service.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import structlog

from app.exceptions import (
    PdfInvalidError,
    PdfNoTextExtractableError,
    PdfPasswordProtectedError,
    PdfTooManyPagesError,
)
from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock

_log = structlog.get_logger(__name__)

# Docling's own logs must not flood service stdout. Setting the level here is
# cheap (it only installs a filter on the root docling logger) and safe to do
# unconditionally even when docling is not installed — Python's logging
# module creates the logger on demand without importing the package.
logging.getLogger("docling").setLevel(logging.WARNING)


@runtime_checkable
class _DoclingTextItemLike(Protocol):
    """Minimum shape the parser needs from one text-bearing item.

    Adapters expose bounding-box coordinates in PDF page coordinates with the
    origin at the bottom-left of the page (`y0 <= y1`, with `y` growing
    upward). Any translation from Docling's native convention lives in the
    adapter, not here.
    """

    @property
    def text(self) -> str: ...
    @property
    def page_number(self) -> int: ...
    @property
    def bbox_x0(self) -> float: ...
    @property
    def bbox_y0(self) -> float: ...
    @property
    def bbox_x1(self) -> float: ...
    @property
    def bbox_y1(self) -> float: ...


@runtime_checkable
class _DoclingDocumentLike(Protocol):
    """Minimum shape the parser needs from a Docling document."""

    @property
    def page_count(self) -> int: ...
    def iter_text_items(self) -> Iterable[_DoclingTextItemLike]: ...


@runtime_checkable
class _DoclingConverterLike(Protocol):
    """Minimum shape the parser needs from a Docling converter.

    `convert` accepts the raw PDF bytes and returns a `_DoclingDocumentLike`.
    Keeping the signature bytes-in / adapter-out means the real Docling
    factory owns every decision about `DocumentStream` wrapping, and the
    parser itself stays agnostic.
    """

    def convert(self, pdf_bytes: bytes) -> _DoclingDocumentLike: ...


DoclingConverterFactory = Callable[[DoclingConfig], _DoclingConverterLike]


@runtime_checkable
class PdfPreflight(Protocol):
    """Validates raw PDF bytes and returns the page count, *before* Docling runs.

    Must raise `PdfInvalidError` for bytes that are not a valid PDF and
    `PdfPasswordProtectedError` for encrypted PDFs. On success, returns the
    PDF's page count so the parser can enforce `max_pdf_pages` *before*
    triggering Docling's full conversion pipeline (which includes OCR and is
    the expensive cost the page-count cap is meant to defend against —
    PDFX-E003-F004 technical constraint).

    The default implementation uses PyMuPDF (`fitz`) and is the only place in
    this file allowed to import `fitz`; unit tests inject a trivial preflight
    (a plain function satisfies the Protocol structurally) that returns a
    chosen page count without loading PyMuPDF.
    """

    def __call__(self, pdf_bytes: bytes) -> int: ...


def _default_pdf_preflight(pdf_bytes: bytes) -> int:
    """Validate PDF bytes using PyMuPDF and return page count.

    PyMuPDF is lazy-imported so unit tests that inject their own preflight
    never trigger the `fitz` import path. This containment mirrors the
    Docling lazy-import strategy used by `_default_converter_factory`.

    Raises `PdfInvalidError` on malformed bytes (narrowly catching PyMuPDF's
    own `FileDataError` / `EmptyFileError` family so unrelated runtime errors
    like `MemoryError` propagate as 500s — "no silent fallbacks" per spec).
    """
    try:
        pymupdf: Any = importlib.import_module("pymupdf")
    except ImportError as exc:  # pragma: no cover - pymupdf is a pinned dep
        msg = (
            "pymupdf is not installed; the default PDF preflight cannot "
            "validate raw bytes. Tests must inject a preflight via "
            "DoclingDocumentParser(pdf_preflight=...)."
        )
        raise RuntimeError(msg) from exc

    # Narrow catch: only PyMuPDF's own data-error hierarchy maps to
    # PdfInvalidError. Anything else (MemoryError, OSError, etc.) propagates
    # as a 500 — "no silent fallbacks" per PDFX-E003-F004 technical constraint.
    # Matching by class name (not by `isinstance` against a getattr result)
    # keeps pyright strict happy while remaining robust to PyMuPDF API drift.
    try:
        doc: Any = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        if type(exc).__name__ not in {"FileDataError", "EmptyFileError"} and not isinstance(
            exc,
            ValueError,
        ):
            raise
        _log.info("pdf_invalid", reason=type(exc).__name__)
        raise PdfInvalidError from exc

    try:
        if bool(doc.needs_pass):
            _log.info("pdf_password_protected")
            raise PdfPasswordProtectedError
        return int(doc.page_count)
    finally:
        doc.close()


def _default_converter_factory(config: DoclingConfig) -> _DoclingConverterLike:
    """Build a real Docling-backed converter adapter honoring `config`.

    Imports of Docling are lazy (`importlib.import_module`) so:

    1. Module load does not pay the Docling startup cost.
    2. Unit tests that inject their own `converter_factory` never trigger
       any Docling code path, even indirectly.
    3. Environments without Docling installed (e.g., CI before PDFX-E001-F002
       lands) can still import this module — the ImportError is only raised
       when someone actually invokes the default factory.

    The returned object conforms to `_DoclingConverterLike` via a small
    adapter class that wraps Docling's real types and exposes the subset
    this parser consumes.
    """
    try:
        base_models: Any = importlib.import_module("docling.datamodel.base_models")
        pipeline_options_mod: Any = importlib.import_module(
            "docling.datamodel.pipeline_options",
        )
        document_converter_mod: Any = importlib.import_module(
            "docling.document_converter",
        )
    except ImportError as exc:
        msg = (
            "docling is not installed; DoclingDocumentParser's default "
            "converter factory cannot build a real Docling pipeline. "
            "Docling becomes a pinned runtime dependency in PDFX-E001-F002. "
            "Until then, unit tests must inject a fake `converter_factory` "
            "via DoclingDocumentParser(converter_factory=...)."
        )
        raise RuntimeError(msg) from exc

    input_format: Any = base_models.InputFormat
    pdf_pipeline_options_cls: Any = pipeline_options_mod.PdfPipelineOptions
    table_structure_options_cls: Any = pipeline_options_mod.TableStructureOptions
    table_former_mode: Any = pipeline_options_mod.TableFormerMode
    easy_ocr_options_cls: Any = pipeline_options_mod.EasyOcrOptions
    document_converter_cls: Any = document_converter_mod.DocumentConverter
    pdf_format_option_cls: Any = document_converter_mod.PdfFormatOption

    # OCR mode mapping, per PDFX-E003-F002 scope:
    #   - "off":   do_ocr=False               (skip OCR entirely)
    #   - "auto":  do_ocr=True, force_full_page_ocr=False
    #              (Docling auto-detects the absence of a text layer and
    #               runs OCR on pages that lack one)
    #   - "force": do_ocr=True, force_full_page_ocr=True
    #              (OCR every page even when a text layer is present)
    do_ocr: bool = config.ocr != "off"
    force_full_page_ocr: bool = config.ocr == "force"
    mode: Any = (
        table_former_mode.ACCURATE if config.table_mode == "accurate" else table_former_mode.FAST
    )

    pipeline_options: Any = pdf_pipeline_options_cls()
    pipeline_options.do_ocr = do_ocr
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options = table_structure_options_cls(
        do_cell_matching=True,
        mode=mode,
    )
    if do_ocr:
        pipeline_options.ocr_options = easy_ocr_options_cls(
            force_full_page_ocr=force_full_page_ocr,
        )

    real_converter: Any = document_converter_cls(
        format_options={
            input_format.PDF: pdf_format_option_cls(
                pipeline_options=pipeline_options,
            ),
        },
    )
    return _RealDoclingConverterAdapter(real_converter)


class _RealDoclingConverterAdapter:
    """Adapts Docling's real `DocumentConverter` to `_DoclingConverterLike`.

    Lives alongside `_default_converter_factory` so all knowledge of Docling's
    public surface stays in one place. The class methods are the single place
    where Docling's actual types are read; everywhere else in the parser the
    code only ever touches our local Protocols.
    """

    def __init__(self, real_converter: Any) -> None:
        self._real_converter: Any = real_converter

    def convert(self, pdf_bytes: bytes) -> _DoclingDocumentLike:
        base_models: Any = importlib.import_module("docling.datamodel.base_models")
        document_stream_cls: Any = base_models.DocumentStream
        source: Any = document_stream_cls(name="input.pdf", stream=io.BytesIO(pdf_bytes))
        result: Any = self._real_converter.convert(source)
        return _RealDoclingDocumentAdapter(result.document)


class _RealDoclingDocumentAdapter:
    """Adapts Docling's `DoclingDocument` to `_DoclingDocumentLike`.

    Implements `iter_text_items` by walking Docling's text-bearing node items
    and translating each provenance entry into a simple flat item that
    exposes bottom-left-origin coordinates. Docling's `BoundingBox` carries a
    `coord_origin` field (`CoordOrigin.TOPLEFT` or `CoordOrigin.BOTTOMLEFT`) —
    TOPLEFT is in fact Docling's *default* for most pipeline outputs — so the
    adapter cannot assume one origin. Every prov bbox is normalized to
    BOTTOMLEFT via `bbox.to_bottom_left_origin(page_height=...)` before
    unpacking, using the owning page's height from `doc.pages[page_no].size`.
    This matches our canonical `BoundingBox` convention (origin bottom-left,
    `y0 <= y1`) and matches PyMuPDF, which the annotator uses downstream
    without further transformation. (GH issue #133.)
    """

    def __init__(self, docling_document: Any) -> None:
        self._docling_document: Any = docling_document

    @property
    def page_count(self) -> int:
        pages: Any = self._docling_document.pages
        return len(pages)

    def iter_text_items(self) -> Iterable[_DoclingTextItemLike]:
        texts: Any = getattr(self._docling_document, "texts", None) or []
        pages: Any = getattr(self._docling_document, "pages", None) or {}
        for text_item in texts:
            item: Any = text_item
            text_value: Any = getattr(item, "text", None)
            provs: Any = getattr(item, "prov", None) or []
            if not text_value or not provs:
                continue
            prov: Any = provs[0]
            page_no: int = int(prov.page_no)
            raw_bbox: Any = prov.bbox
            # Docling's `CoordOrigin` is a `str, Enum` whose members stringify
            # to "TOPLEFT" / "BOTTOMLEFT". Compare against the string form so
            # the check is robust to test doubles that pass plain strings and
            # to the real enum — both satisfy `str(origin) == "TOPLEFT"`.
            origin: Any = getattr(raw_bbox, "coord_origin", None)
            needs_flip: bool = origin is not None and str(origin).endswith("TOPLEFT")
            if needs_flip:
                page: Any = pages.get(page_no) if hasattr(pages, "get") else pages[page_no]
                page_height: float = float(page.size.height)
                bbox: Any = raw_bbox.to_bottom_left_origin(page_height=page_height)
            else:
                bbox = raw_bbox
            yield _FlatDoclingTextItem(
                text=str(text_value),
                page_number=page_no,
                bbox_x0=float(bbox.l),
                bbox_y0=float(bbox.b),
                bbox_x1=float(bbox.r),
                bbox_y1=float(bbox.t),
            )


@dataclass(frozen=True)
class _FlatDoclingTextItem:
    """Plain-data implementation of `_DoclingTextItemLike`.

    The real-Docling adapter yields instances of this class to bridge
    Docling's own types into the parser's Protocol shape. It is a frozen
    dataclass so callers cannot mutate a yielded item while the parser is
    still walking the document.
    """

    text: str
    page_number: int
    bbox_x0: float
    bbox_y0: float
    bbox_x1: float
    bbox_y1: float


class DoclingDocumentParser:
    """Concrete `DocumentParser` backed by Docling.

    Usage:
        parser = DoclingDocumentParser()  # uses real Docling via lazy import
        parsed = await parser.parse(pdf_bytes, DoclingConfig(...))

    In unit tests, pass a fake `converter_factory` to avoid any real Docling
    code path:
        parser = DoclingDocumentParser(converter_factory=fake_factory)

    The parser is stateless: every call to `parse` constructs a fresh
    converter via the factory, converts synchronously on a worker thread,
    and translates the result into a `ParsedDocument`. No caching, no
    shared mutable state between calls.
    """

    def __init__(
        self,
        *,
        converter_factory: DoclingConverterFactory | None = None,
        pdf_preflight: PdfPreflight | None = None,
        max_pdf_pages: int = 200,
    ) -> None:
        self._converter_factory: DoclingConverterFactory = (
            converter_factory if converter_factory is not None else _default_converter_factory
        )
        self._pdf_preflight: PdfPreflight = (
            pdf_preflight if pdf_preflight is not None else _default_pdf_preflight
        )
        self._max_pdf_pages: int = max_pdf_pages

    async def parse(
        self,
        pdf_bytes: bytes,
        docling_config: DoclingConfig,
    ) -> ParsedDocument:
        # Preflight: validates format, rejects encrypted, returns page count
        # BEFORE Docling runs. This order is load-bearing — Docling's full
        # pipeline (layout analysis, OCR) is the expensive cost the page-count
        # cap is meant to defend against (PDFX-E003-F004).
        #
        # The default preflight opens the PDF with PyMuPDF (a blocking C
        # call), so we offload it to a worker thread to keep the FastAPI
        # event loop responsive for concurrent requests. Test-injected
        # preflights are also offloaded — the parser does not distinguish
        # between real and fake preflights at the call site.
        preflight_page_count = await asyncio.to_thread(self._pdf_preflight, pdf_bytes)

        if preflight_page_count > self._max_pdf_pages:
            _log.info(
                "pdf_too_many_pages",
                limit=self._max_pdf_pages,
                actual=preflight_page_count,
            )
            raise PdfTooManyPagesError(
                limit=self._max_pdf_pages,
                actual=preflight_page_count,
            )

        def _build_and_convert() -> _DoclingDocumentLike:
            converter = self._converter_factory(docling_config)
            return converter.convert(pdf_bytes)

        document = await asyncio.to_thread(_build_and_convert)

        parsed = self._to_parsed_document(document)
        if not parsed.blocks:
            _log.info("pdf_no_text_extractable", page_count=document.page_count)
            raise PdfNoTextExtractableError

        return parsed

    @staticmethod
    def _to_parsed_document(document: _DoclingDocumentLike) -> ParsedDocument:
        blocks: list[TextBlock] = []
        per_page_index: dict[int, int] = {}
        for item in document.iter_text_items():
            page_number = item.page_number
            index = per_page_index.get(page_number, 0)
            per_page_index[page_number] = index + 1
            bbox = BoundingBox(
                x0=item.bbox_x0,
                y0=item.bbox_y0,
                x1=item.bbox_x1,
                y1=item.bbox_y1,
            )
            blocks.append(
                TextBlock(
                    text=item.text,
                    page_number=page_number,
                    bbox=bbox,
                    block_id=f"p{page_number}_b{index}",
                ),
            )
        return ParsedDocument(blocks=tuple(blocks), page_count=document.page_count)

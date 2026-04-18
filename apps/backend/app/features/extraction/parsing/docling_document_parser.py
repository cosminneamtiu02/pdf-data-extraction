"""DoclingDocumentParser: the public ``DocumentParser`` backed by Docling.

This file is the parser's public face. The Docling-touching adapters and
factory live in sibling files (``_real_docling_converter_adapter.py``,
``_real_docling_document_adapter.py``, ``_flat_docling_text_item.py``); the
Protocols they implement live in ``_docling_*.py`` / ``pdf_preflight.py``.
Splitting the file was driven by issue #159 (CLAUDE.md Sacred Rule #1 —
one class per file) — import-linter contract C3 was expanded from a single
file to a specific allow-list of three sibling Docling files so the
Docling containment boundary still holds across the new layout. The three
files are this one, ``_real_docling_converter_adapter.py``, and
``_real_docling_document_adapter.py``.

PyMuPDF (``fitz``) is still imported here because ``_default_pdf_preflight``
uses it to validate PDF bytes + detect encryption *before* Docling runs.
Import-linter contract C4 whitelists this file for pymupdf/fitz.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import TYPE_CHECKING, Any

import structlog

from app.exceptions import (
    PdfInvalidError,
    PdfNoTextExtractableError,
    PdfParserUnavailableError,
    PdfPasswordProtectedError,
    PdfTooManyPagesError,
)
from app.features.extraction.parsing._real_docling_converter_adapter import (
    DoclingConverterFactory,
    default_converter_factory,
)
from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock

if TYPE_CHECKING:
    from app.features.extraction.parsing._docling_document_like import DoclingDocumentLike
    from app.features.extraction.parsing.docling_config import DoclingConfig
    from app.features.extraction.parsing.pdf_preflight import PdfPreflight

_log = structlog.get_logger(__name__)

# Docling's own logs must not flood service stdout. Setting the level here is
# cheap (it only installs a filter on the root docling logger) and safe to do
# unconditionally even when docling is not installed — Python's logging
# module creates the logger on demand without importing the package.
logging.getLogger("docling").setLevel(logging.WARNING)


def _default_pdf_preflight(pdf_bytes: bytes) -> int:
    """Validate PDF bytes using PyMuPDF and return page count.

    PyMuPDF is lazy-imported so unit tests that inject their own preflight
    never trigger the ``fitz`` import path. This containment mirrors the
    Docling lazy-import strategy used by ``default_converter_factory``.

    Raises ``PdfInvalidError`` on malformed bytes (narrowly catching PyMuPDF's
    own ``FileDataError`` / ``EmptyFileError`` family so unrelated runtime
    errors like ``MemoryError`` propagate as 500s — "no silent fallbacks" per
    spec).
    """
    try:
        pymupdf: Any = importlib.import_module("pymupdf")
    except ImportError as exc:
        _log.error(
            "parser_dependency_unavailable",
            dependency="pymupdf",
            detail=(
                "pymupdf is not installed; the default PDF preflight cannot "
                "validate raw bytes. Tests must inject a preflight via "
                "DoclingDocumentParser(pdf_preflight=...)."
            ),
        )
        raise PdfParserUnavailableError(dependency="pymupdf") from exc

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


class DoclingDocumentParser:
    """Concrete ``DocumentParser`` backed by Docling.

    Usage::

        parser = DoclingDocumentParser()  # uses real Docling via lazy import
        parsed = await parser.parse(pdf_bytes, DoclingConfig(...))

    In unit tests, pass a fake ``converter_factory`` to avoid any real Docling
    code path::

        parser = DoclingDocumentParser(converter_factory=fake_factory)

    The parser is stateless: every call to ``parse`` constructs a fresh
    converter via the factory, converts synchronously on a worker thread,
    and translates the result into a ``ParsedDocument``. No caching, no
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
            converter_factory if converter_factory is not None else default_converter_factory
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

        def _build_and_convert() -> DoclingDocumentLike:
            converter = self._converter_factory(docling_config)
            return converter.convert(pdf_bytes)

        document = await asyncio.to_thread(_build_and_convert)

        parsed = self._to_parsed_document(document)
        if not parsed.blocks:
            _log.info("pdf_no_text_extractable", page_count=document.page_count)
            raise PdfNoTextExtractableError

        return parsed

    @staticmethod
    def _to_parsed_document(document: DoclingDocumentLike) -> ParsedDocument:
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

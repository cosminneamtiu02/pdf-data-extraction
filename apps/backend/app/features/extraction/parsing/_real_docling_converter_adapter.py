"""Real-Docling converter adapter and its factory.

This file is the parser layer's concrete Docling import/factory entry
point, one of three files on import-linter contract C3's allow-list
(alongside ``_real_docling_document_adapter.py`` and the parser coordinator
``docling_document_parser.py``). The ``RealDoclingConverterAdapter`` class
wraps Docling's concrete ``DocumentConverter`` and exposes the subset the
parser consumes via the local ``DoclingConverterLike`` Protocol. The
``default_converter_factory`` function builds the adapter by lazy-importing
Docling so unit tests (which inject fake factories) never trigger the real
Docling import path and module load stays cheap.

The ``DoclingConverterFactory`` type alias lives here because it is the
return-type signature of ``default_converter_factory``; callers in
``docling_document_parser.py`` import the alias to type their
``converter_factory`` constructor parameter.
"""

from __future__ import annotations

import importlib
import io
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from app.exceptions import PdfParserUnavailableError
from app.features.extraction.parsing._docling_converter_like import DoclingConverterLike
from app.features.extraction.parsing._real_docling_document_adapter import (
    RealDoclingDocumentAdapter,
)
from app.features.extraction.parsing.docling_config import DoclingConfig

if TYPE_CHECKING:
    from app.features.extraction.parsing._docling_document_like import DoclingDocumentLike

_log = structlog.get_logger(__name__)


DoclingConverterFactory = Callable[[DoclingConfig], DoclingConverterLike]


class RealDoclingConverterAdapter:
    """Adapts Docling's real ``DocumentConverter`` to ``DoclingConverterLike``.

    The class methods are the single place (together with
    ``default_converter_factory`` below) where Docling's actual types are
    read; everywhere else in the parser the code only ever touches our
    local Protocols.
    """

    def __init__(self, real_converter: Any) -> None:
        self._real_converter: Any = real_converter

    def convert(self, pdf_bytes: bytes) -> DoclingDocumentLike:
        base_models: Any = importlib.import_module("docling.datamodel.base_models")
        document_stream_cls: Any = base_models.DocumentStream
        # Issue #383: Docling may surface the DocumentStream ``name`` in its
        # logging/debug stream. A hardcoded ``input.pdf`` makes concurrent-
        # request logs impossible to correlate. Prefer the structlog
        # contextvars ``request_id`` bound by ``RequestIdMiddleware``; fall
        # back to a fresh uuid hex otherwise. The ``.pdf`` suffix is preserved
        # so Docling's filename-based format detection still routes to the
        # PDF backend. Non-string ``request_id`` values (defensive guard
        # against an int or other type leaking through a binder) fall back
        # to the uuid path rather than propagating into Docling.
        context_request_id: Any = structlog.contextvars.get_contextvars().get("request_id")
        stream_name: str = (
            f"{context_request_id}.pdf"
            if isinstance(context_request_id, str) and context_request_id
            else f"{uuid.uuid4().hex}.pdf"
        )
        source: Any = document_stream_cls(name=stream_name, stream=io.BytesIO(pdf_bytes))
        result: Any = self._real_converter.convert(source)
        return RealDoclingDocumentAdapter(result.document)


def default_converter_factory(config: DoclingConfig) -> DoclingConverterLike:
    """Build a real Docling-backed converter adapter honoring ``config``.

    Imports of Docling are lazy (``importlib.import_module``) so:

    1. Module load does not pay the Docling startup cost.
    2. Unit tests that inject their own ``converter_factory`` never trigger
       any Docling code path, even indirectly.
    3. Environments without Docling installed (e.g., CI before PDFX-E001-F002
       lands) can still import this module — the ImportError is only raised
       when someone actually invokes the default factory.

    The returned object conforms to ``DoclingConverterLike`` via a small
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
        _log.error(
            "parser_dependency_unavailable",
            dependency="docling",
            detail=(
                "docling is not installed; DoclingDocumentParser's default "
                "converter factory cannot build a real Docling pipeline. "
                "Docling becomes a pinned runtime dependency in PDFX-E001-F002. "
                "Until then, unit tests must inject a fake `converter_factory` "
                "via DoclingDocumentParser(converter_factory=...)."
            ),
        )
        raise PdfParserUnavailableError(dependency="docling") from exc

    input_format: Any = base_models.InputFormat
    pdf_pipeline_options_cls: Any = pipeline_options_mod.PdfPipelineOptions
    table_structure_options_cls: Any = pipeline_options_mod.TableStructureOptions
    table_former_mode: Any = pipeline_options_mod.TableFormerMode
    # Tesseract via CLI — not EasyOCR. ``EasyOcrOptions`` triggered
    # ``ImportError: EasyOCR is not installed`` on any runtime OCR path because
    # ``easyocr`` is not a Docling base dependency (it is a heavy torch/opencv
    # extra that would undo the CUDA-bloat work tracked in issue #139).
    # ``TesseractCliOcrOptions`` shells out to the system ``tesseract`` binary
    # and needs no Python bindings — the Dockerfile ships ``tesseract-ocr`` +
    # ``tesseract-ocr-eng`` via apt. See docs/decisions.md ADR-013 and
    # issue #106.
    tesseract_cli_ocr_options_cls: Any = pipeline_options_mod.TesseractCliOcrOptions
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
        pipeline_options.ocr_options = tesseract_cli_ocr_options_cls(
            force_full_page_ocr=force_full_page_ocr,
        )

    real_converter: Any = document_converter_cls(
        format_options={
            input_format.PDF: pdf_format_option_cls(
                pipeline_options=pipeline_options,
            ),
        },
    )
    return RealDoclingConverterAdapter(real_converter)

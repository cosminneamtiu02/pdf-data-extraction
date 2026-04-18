"""Public Protocol for the PDF preflight step used by ``DoclingDocumentParser``."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PdfPreflight(Protocol):
    """Validates raw PDF bytes and returns the page count, *before* Docling runs.

    Must raise ``PdfInvalidError`` for bytes that are not a valid PDF and
    ``PdfPasswordProtectedError`` for encrypted PDFs. On success, returns the
    PDF's page count so the parser can enforce ``max_pdf_pages`` *before*
    triggering Docling's full conversion pipeline (which includes OCR and is
    the expensive cost the page-count cap is meant to defend against —
    PDFX-E003-F004 technical constraint).

    The default implementation (``_default_pdf_preflight`` in
    ``docling_document_parser.py``) uses PyMuPDF (``fitz``); unit tests inject
    a trivial preflight (a plain function satisfies the Protocol structurally)
    that returns a chosen page count without loading PyMuPDF.
    """

    def __call__(self, pdf_bytes: bytes) -> int: ...

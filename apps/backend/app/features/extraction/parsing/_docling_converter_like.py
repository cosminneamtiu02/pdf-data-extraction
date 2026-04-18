"""Protocol describing the minimum shape the parser needs from a Docling converter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.features.extraction.parsing._docling_document_like import DoclingDocumentLike


@runtime_checkable
class DoclingConverterLike(Protocol):
    """Minimum shape the parser needs from a Docling converter.

    ``convert`` accepts the raw PDF bytes and returns a ``DoclingDocumentLike``.
    Keeping the signature bytes-in / adapter-out means the real Docling
    factory owns every decision about ``DocumentStream`` wrapping, and the
    parser itself stays agnostic.
    """

    def convert(self, pdf_bytes: bytes) -> DoclingDocumentLike: ...

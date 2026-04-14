"""DocumentParser: the parsing-layer abstraction Protocol.

A concrete implementation (PDFX-E003-F002, `DoclingDocumentParser`) is the only
file in the feature permitted to import Docling. Every consumer of parsed
documents — the concatenator, the span resolver, the annotator — depends on
this Protocol so they can be unit-tested against fake parsers without running
Docling or parsing real PDFs.
"""

from typing import Protocol, runtime_checkable

from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.parsed_document import ParsedDocument


@runtime_checkable
class DocumentParser(Protocol):
    async def parse(
        self,
        pdf_bytes: bytes,
        docling_config: DoclingConfig,
    ) -> ParsedDocument: ...

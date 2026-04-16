"""ExtractionResult — internal pipeline output (PDFX-E006-F002).

Not serialized directly; the router (PDFX-E006-F003) unpacks this into
the right HTTP response based on output_mode.
"""

from dataclasses import dataclass

from app.features.extraction.schemas.extract_response import ExtractResponse


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Pipeline output: the assembled response plus optional annotated PDF."""

    response: ExtractResponse
    annotated_pdf_bytes: bytes | None

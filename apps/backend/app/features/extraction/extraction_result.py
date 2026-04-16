"""The internal result of a full extraction pipeline run.

``ExtractionResult`` is the boundary type between ``ExtractionService`` and the
router.  The service populates it; the router unpacks it into the appropriate
HTTP response shape per ``output_mode``.

This is NOT a Pydantic model — it is a frozen dataclass because it is an
internal pipeline artefact that never crosses the serialization boundary.
``ExtractResponse`` (the Pydantic schema) is one of its fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.features.extraction.schemas.extract_response import ExtractResponse


@dataclass(frozen=True)
class ExtractionResult:
    """Immutable result of ``ExtractionService.extract``.

    Attributes:
        response: The structured JSON response suitable for serialization.
        annotated_pdf_bytes: The annotated PDF binary, or ``None`` when the
            caller requested ``output_mode=JSON_ONLY``.
    """

    response: ExtractResponse
    annotated_pdf_bytes: bytes | None

"""ExtractionService — top-level pipeline orchestrator.

This is a STUB created by PDFX-E006-F003 (router).  The full implementation
is delivered by PDFX-E006-F002.  Only the public interface used by the router
is defined here so that the router and its tests can compile and run against
mocks.

The ``extract`` method signature is the contract between the service and the
router.  F002 MUST keep this signature intact when it replaces the stub with
the real orchestration logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.features.extraction.extraction_result import ExtractionResult
    from app.features.extraction.schemas.output_mode import OutputMode

_STUB_MSG = "ExtractionService.extract is a stub — implement in PDFX-E006-F002"


class ExtractionService:
    """Orchestrate the full extraction pipeline.

    Constructor parameters will be filled in by PDFX-E006-F002.  The router
    only depends on the ``extract`` method signature.
    """

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    async def extract(
        self,
        pdf_bytes: bytes,
        skill_name: str,
        skill_version: str,
        output_mode: OutputMode,
    ) -> ExtractionResult:
        """Run the extraction pipeline and return the result.

        This stub raises ``NotImplementedError``.  The full implementation is
        delivered by PDFX-E006-F002 which wires skill resolution, parsing,
        text concatenation, extraction, span resolution, and annotation.
        """
        raise NotImplementedError(_STUB_MSG)

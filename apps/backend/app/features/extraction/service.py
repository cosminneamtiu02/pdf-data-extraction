"""ExtractionService — pipeline orchestrator (PDFX-E006-F002).

Stitches together skill lookup, PDF parsing, text concatenation,
LLM-driven extraction, coordinate resolution, and PDF annotation
under a single ``asyncio.timeout`` budget.

This is the only class that knows how the pipeline is composed.
Every component below it is independently testable; the service
is what makes them a pipeline.

**Best-effort timeout.** ``asyncio.timeout`` cancels cooperative awaits
but cannot stop CPU work already running in background threads (e.g.
``DoclingDocumentParser.parse`` and ``ExtractionEngine.extract`` both
use ``asyncio.to_thread``). A timed-out request returns 504 while
the thread may still be finishing. The unfinished background work may
overlap with later requests unless concurrency is explicitly limited
elsewhere; once that work completes, its result is discarded.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from app.exceptions import IntelligenceTimeoutError, StructuredOutputFailedError
from app.features.extraction.extraction.extraction_engine import declared_field_names
from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.parsing.docling_config_merger import merge_docling_config
from app.features.extraction.schemas.extract_response import ExtractResponse
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
from app.features.extraction.schemas.field_status import FieldStatus
from app.features.extraction.schemas.output_mode import OutputMode

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.features.extraction.annotation.pdf_annotator import PdfAnnotator
    from app.features.extraction.coordinates.span_resolver import SpanResolver
    from app.features.extraction.coordinates.text_concatenator import TextConcatenator
    from app.features.extraction.extraction.extraction_engine import ExtractionEngine
    from app.features.extraction.intelligence.intelligence_provider import (
        IntelligenceProvider,
    )
    from app.features.extraction.parsing.document_parser import DocumentParser
    from app.features.extraction.skills.skill_manifest import SkillManifest


class ExtractionService:
    """Orchestrate the full extraction pipeline under an end-to-end timeout."""

    def __init__(  # noqa: PLR0913 — orchestrator takes all pipeline components
        self,
        *,
        skill_manifest: SkillManifest,
        document_parser: DocumentParser,
        text_concatenator: TextConcatenator,
        extraction_engine: ExtractionEngine,
        span_resolver: SpanResolver,
        pdf_annotator: PdfAnnotator,
        intelligence_provider: IntelligenceProvider,
        settings: Settings,
    ) -> None:
        self._skill_manifest = skill_manifest
        self._document_parser = document_parser
        self._text_concatenator = text_concatenator
        self._extraction_engine = extraction_engine
        self._span_resolver = span_resolver
        self._pdf_annotator = pdf_annotator
        self._intelligence_provider = intelligence_provider
        self._settings = settings
        self._timeout_seconds = settings.extraction_timeout_seconds

    async def extract(
        self,
        pdf_bytes: bytes,
        skill_name: str,
        skill_version: str,
        output_mode: OutputMode,
    ) -> ExtractionResult:
        """Run the full pipeline and return the extraction result.

        Raises:
            IntelligenceTimeoutError: pipeline exceeded the timeout budget.
            StructuredOutputFailedError: every declared field failed extraction,
                or the skill declared zero fields.
            SkillNotFoundError: requested skill is not in the manifest.
            Other DomainError subclasses: propagated from downstream components.
        """
        t0 = time.monotonic()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                # 1. Resolve skill
                skill = self._skill_manifest.lookup(skill_name, skill_version)

                # 2. Merge docling config
                merged_config = merge_docling_config(self._settings, skill.docling_config)

                # 3. Parse PDF
                parsed_doc = await self._document_parser.parse(pdf_bytes, merged_config)

                # 4. Concatenate text
                concatenated_text, offset_index = self._text_concatenator.concatenate(
                    parsed_doc,
                )

                # 5. Extract via LLM
                raw_extractions = await self._extraction_engine.extract(
                    concatenated_text,
                    skill,
                    self._intelligence_provider,
                )

                # 6. Resolve spans (unconditional for all output modes)
                fields = list(declared_field_names(skill))
                extracted_fields = self._span_resolver.resolve(
                    raw_extractions,
                    offset_index,
                    parsed_doc,
                    fields,
                )

                # 7. All-failed check (before annotation to avoid wasted I/O).
                #    Empty extracted_fields (zero declared fields) is also total
                #    failure — the spec says "zero extracted fields → 502".
                if not any(f.status == FieldStatus.extracted for f in extracted_fields):
                    raise StructuredOutputFailedError

                # 8. Annotate (conditional)
                annotated_pdf: bytes | None = None
                if output_mode != OutputMode.JSON_ONLY:
                    annotated_pdf = await self._pdf_annotator.annotate(
                        pdf_bytes,
                        extracted_fields,
                    )

                # 9. Assemble response
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                attempts_by_name = {r.field_name: r.attempts for r in raw_extractions}
                metadata = ExtractionMetadata(
                    page_count=parsed_doc.page_count,
                    duration_ms=elapsed_ms,
                    attempts_per_field={name: attempts_by_name.get(name, 1) for name in fields},
                )
                response = ExtractResponse(
                    skill_name=skill_name,
                    skill_version=skill.version,
                    fields={f.name: f for f in extracted_fields},
                    metadata=metadata,
                )
                return ExtractionResult(
                    response=response,
                    annotated_pdf_bytes=annotated_pdf,
                )
        except TimeoutError:
            raise IntelligenceTimeoutError(
                budget_seconds=self._timeout_seconds,
            ) from None

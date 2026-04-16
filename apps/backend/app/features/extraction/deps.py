"""Extraction-feature dependency factories (PDFX-E006-F002).

Wires ``ExtractionService`` from the shared infrastructure deps in
``app.api.deps`` plus stateless feature-internal components. The
service is lazily constructed and cached on ``app.state`` per the
same double-checked locking pattern used in ``app.api.deps``.
"""

import threading

from fastapi import Request

from app.api.deps import get_document_parser, get_intelligence_provider, get_settings
from app.features.extraction.annotation.pdf_annotator import PdfAnnotator
from app.features.extraction.coordinates.span_resolver import SpanResolver
from app.features.extraction.coordinates.text_concatenator import TextConcatenator
from app.features.extraction.extraction.extraction_engine import ExtractionEngine
from app.features.extraction.service import ExtractionService

_dep_init_lock = threading.RLock()


def get_extraction_service(request: Request) -> ExtractionService:
    """Return (and lazily cache) the extraction service for this app."""
    state = request.app.state
    service: ExtractionService | None = getattr(state, "extraction_service", None)
    if service is None:
        with _dep_init_lock:
            service = getattr(state, "extraction_service", None)
            if service is None:
                settings = get_settings(request)
                service = ExtractionService(
                    skill_manifest=state.skill_manifest,
                    document_parser=get_document_parser(request),
                    text_concatenator=TextConcatenator(),
                    extraction_engine=ExtractionEngine(),
                    span_resolver=SpanResolver(),
                    pdf_annotator=PdfAnnotator(),
                    intelligence_provider=get_intelligence_provider(request),
                    settings=settings,
                )
                state.extraction_service = service
    return service

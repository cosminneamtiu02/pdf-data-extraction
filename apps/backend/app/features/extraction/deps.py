"""Extraction-feature dependency factories (PDFX-E006-F002).

Each pipeline component is produced by its own ``Depends`` factory so
integration tests and downstream routers can override individual pieces
via ``app.dependency_overrides``.

The service and its components are lazily constructed and cached on
``app.state``. The module-level ``_dep_init_lock`` serializes first-
access construction only; subsequent calls are a fast ``getattr`` check.
"""

import threading

from fastapi import Request

from app.api.deps import get_document_parser, get_intelligence_provider, get_settings
from app.features.extraction.annotation.pdf_annotator import PdfAnnotator
from app.features.extraction.coordinates.span_resolver import SpanResolver
from app.features.extraction.coordinates.text_concatenator import TextConcatenator
from app.features.extraction.extraction.extraction_engine import ExtractionEngine
from app.features.extraction.service import ExtractionService
from app.features.extraction.skills.skill_manifest import SkillManifest

_dep_init_lock = threading.RLock()


def get_skill_manifest(request: Request) -> SkillManifest:
    """Return the manifest built at startup by ``create_app``."""
    return request.app.state.skill_manifest


def get_text_concatenator(request: Request) -> TextConcatenator:
    """Return (and lazily cache) the text concatenator for this app."""
    state = request.app.state
    concatenator: TextConcatenator | None = getattr(state, "text_concatenator", None)
    if concatenator is None:
        with _dep_init_lock:
            concatenator = getattr(state, "text_concatenator", None)
            if concatenator is None:
                concatenator = TextConcatenator()
                state.text_concatenator = concatenator
    return concatenator


def get_extraction_engine(request: Request) -> ExtractionEngine:
    """Return (and lazily cache) the extraction engine for this app."""
    state = request.app.state
    engine: ExtractionEngine | None = getattr(state, "extraction_engine", None)
    if engine is None:
        with _dep_init_lock:
            engine = getattr(state, "extraction_engine", None)
            if engine is None:
                engine = ExtractionEngine()
                state.extraction_engine = engine
    return engine


def get_span_resolver(request: Request) -> SpanResolver:
    """Return (and lazily cache) the span resolver for this app."""
    state = request.app.state
    resolver: SpanResolver | None = getattr(state, "span_resolver", None)
    if resolver is None:
        with _dep_init_lock:
            resolver = getattr(state, "span_resolver", None)
            if resolver is None:
                resolver = SpanResolver()
                state.span_resolver = resolver
    return resolver


def get_pdf_annotator(request: Request) -> PdfAnnotator:
    """Return (and lazily cache) the PDF annotator for this app."""
    state = request.app.state
    annotator: PdfAnnotator | None = getattr(state, "pdf_annotator", None)
    if annotator is None:
        with _dep_init_lock:
            annotator = getattr(state, "pdf_annotator", None)
            if annotator is None:
                annotator = PdfAnnotator()
                state.pdf_annotator = annotator
    return annotator


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
                    skill_manifest=get_skill_manifest(request),
                    document_parser=get_document_parser(request),
                    text_concatenator=get_text_concatenator(request),
                    extraction_engine=get_extraction_engine(request),
                    span_resolver=get_span_resolver(request),
                    pdf_annotator=get_pdf_annotator(request),
                    intelligence_provider=get_intelligence_provider(request),
                    settings=settings,
                )
                state.extraction_service = service
    return service

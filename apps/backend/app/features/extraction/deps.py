"""Extraction-feature per-component dependency factories (PDFX-E006-F002).

These factories exist so integration tests can override individual pipeline
components via ``app.dependency_overrides[get_span_resolver] = ...`` without
replacing the entire ``ExtractionService``.

The canonical ``get_extraction_service`` factory lives in ``app.api.deps``
(where the router imports it).  This module provides the finer-grained
component factories only.
"""

import threading

from fastapi import Request

from app.features.extraction.annotation.pdf_annotator import PdfAnnotator
from app.features.extraction.coordinates.span_resolver import SpanResolver
from app.features.extraction.coordinates.text_concatenator import TextConcatenator
from app.features.extraction.extraction.extraction_engine import ExtractionEngine
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
                # Pass settings so the engine can bound the per-prompt
                # `future.result()` blocking call inside
                # `_ValidatingLangExtractAdapter.infer` (issue #152).
                engine = ExtractionEngine(settings=state.settings)
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

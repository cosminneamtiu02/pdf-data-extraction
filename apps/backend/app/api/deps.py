"""Shared FastAPI dependencies.

These factories read through `request.app.state`, which is the
FastAPI-idiomatic way to bind process-scoped singletons to a specific app
instance. `create_app(settings=...)` is the supported test seam for
integration tests that need custom configuration; these dependencies must
honor that seam rather than fall back to a module-level `lru_cache` (which
would ignore the per-app override and hand every test the same env-derived
defaults). `Settings` is instantiated in `create_app` and placed on
`app.state.settings`; heavier collaborators are lazily built on first access
and cached on `app.state` so repeated requests to the same app share one
instance.

Concurrency note: the lazy-init paths use double-checked locking guarded
by a module-level `threading.RLock`. Without the lock, two concurrent
first-requests on the same app could both observe `None` on `app.state`,
both build a fresh dependency, and only the second's would be stored —
the first's `OllamaGemmaProvider` would leak its open `httpx.AsyncClient`
because lifespan cleanup only sees the stored instance. The lock is held
only for the brief construction critical section, and it is re-entrant
because `get_intelligence_provider()` builds the validator through the same
guard when the provider is the first dependency touched.
"""

import threading
from functools import lru_cache

from fastapi import Request

from app.api.probe_cache import ProbeCache
from app.core.config import Settings

# Pipeline component imports — these are lightweight classes (no heavy
# transitive deps).  PdfAnnotator pulls in pymupdf at import time, which
# is acceptable: pymupdf is a direct dependency and is needed at first
# extraction request anyway.
from app.features.extraction.annotation.pdf_annotator import PdfAnnotator
from app.features.extraction.coordinates.span_resolver import SpanResolver
from app.features.extraction.coordinates.text_concatenator import TextConcatenator
from app.features.extraction.extraction.extraction_engine import ExtractionEngine
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.ollama_gemma_provider import (
    OllamaGemmaProvider,
    build_tags_url,
)
from app.features.extraction.intelligence.ollama_health_probe import (
    OllamaHealthProbe,
)
from app.features.extraction.intelligence.structured_output_validator import (
    StructuredOutputValidator,
)
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
)
from app.features.extraction.service import ExtractionService

_dep_init_lock = threading.RLock()


def get_settings(request: Request) -> Settings:
    """Return the Settings instance `create_app` bound to this app."""
    return request.app.state.settings


@lru_cache(maxsize=1)
def get_correction_prompt_builder() -> CorrectionPromptBuilder:
    return CorrectionPromptBuilder()


def get_structured_output_validator(request: Request) -> StructuredOutputValidator:
    """Return (and lazily cache) the validator bound to this app instance."""
    state = request.app.state
    validator: StructuredOutputValidator | None = getattr(
        state,
        "structured_output_validator",
        None,
    )
    if validator is None:
        with _dep_init_lock:
            # Re-read inside the critical section: another thread may have
            # constructed the validator while we were waiting for the lock.
            validator = getattr(state, "structured_output_validator", None)
            if validator is None:
                validator = StructuredOutputValidator(
                    settings=get_settings(request),
                    correction_prompt_builder=get_correction_prompt_builder(),
                )
                state.structured_output_validator = validator
    return validator


def get_intelligence_provider(request: Request) -> OllamaGemmaProvider:
    """Return (and lazily cache) the provider bound to this app instance."""
    state = request.app.state
    provider: OllamaGemmaProvider | None = getattr(
        state,
        "intelligence_provider",
        None,
    )
    if provider is None:
        with _dep_init_lock:
            provider = getattr(state, "intelligence_provider", None)
            if provider is None:
                provider = OllamaGemmaProvider(
                    settings=get_settings(request),
                    validator=get_structured_output_validator(request),
                )
                state.intelligence_provider = provider
    return provider


def get_document_parser(request: Request) -> DoclingDocumentParser:
    """Return (and lazily cache) the parser bound to this app instance.

    This factory is the integration seam that makes `MAX_PDF_PAGES` a real
    runtime knob. Docling's lazy import means constructing the parser here
    does not pull in Docling, so module load and unit-test startup stay cheap.
    """
    state = request.app.state
    parser: DoclingDocumentParser | None = getattr(state, "document_parser", None)
    if parser is None:
        with _dep_init_lock:
            parser = getattr(state, "document_parser", None)
            if parser is None:
                parser = DoclingDocumentParser(
                    max_pdf_pages=get_settings(request).max_pdf_pages,
                )
                state.document_parser = parser
    return parser


def get_extraction_service(request: Request) -> ExtractionService:
    """Return (and lazily cache) the extraction service bound to this app."""
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


def get_ollama_health_probe(request: Request) -> OllamaHealthProbe:
    """Return (and lazily cache) the health probe bound to this app instance.

    The tags URL is built here (reusing ``build_tags_url`` from the
    provider module) so the probe class does not duplicate the URL
    construction logic.
    """
    state = request.app.state
    probe: OllamaHealthProbe | None = getattr(state, "ollama_health_probe", None)
    if probe is None:
        with _dep_init_lock:
            probe = getattr(state, "ollama_health_probe", None)
            if probe is None:
                settings = get_settings(request)
                probe = OllamaHealthProbe(
                    tags_url=build_tags_url(settings.ollama_base_url),
                    timeout_seconds=settings.ollama_probe_timeout_seconds,
                )
                state.ollama_health_probe = probe
    return probe


def get_probe_cache(request: Request) -> ProbeCache:
    """Return (and lazily cache) the readiness probe cache."""
    state = request.app.state
    cache: ProbeCache | None = getattr(state, "probe_cache", None)
    if cache is None:
        with _dep_init_lock:
            cache = getattr(state, "probe_cache", None)
            if cache is None:
                cache = ProbeCache(
                    probe=get_ollama_health_probe(request),
                    ttl_seconds=get_settings(request).ollama_probe_ttl_seconds,
                )
                state.probe_cache = cache
    return cache

"""Shared FastAPI dependencies.

All three of these factories read through `request.app.state`, which is the
FastAPI-idiomatic way to bind process-scoped singletons to a specific app
instance. `create_app(settings=...)` is the supported test seam for
integration tests that need custom configuration; these dependencies must
honor that seam rather than fall back to a module-level `lru_cache` (which
would ignore the per-app override and hand every test the same env-derived
defaults). `Settings` is instantiated in `create_app` and placed on
`app.state.settings`; `StructuredOutputValidator` and `OllamaGemmaProvider`
are lazily built on first access and cached on `app.state` so repeated
requests to the same app share one instance.

Concurrency note: the lazy-init paths use double-checked locking guarded
by a module-level `threading.Lock`. Without the lock, two concurrent
first-requests on the same app could both observe `None` on `app.state`,
both build a fresh dependency, and only the second's would be stored —
the first's `OllamaGemmaProvider` would leak its open `httpx.AsyncClient`
because lifespan cleanup only sees the stored instance. The lock is held
only for the brief construction critical section, so contention beyond
the very first request per app is zero.
"""

import threading
from functools import lru_cache

from fastapi import Request

from app.core.config import Settings
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.ollama_gemma_provider import (
    OllamaGemmaProvider,
)
from app.features.extraction.intelligence.structured_output_validator import (
    StructuredOutputValidator,
)

_dep_init_lock = threading.Lock()


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

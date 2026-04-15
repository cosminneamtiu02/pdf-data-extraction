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
"""

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


def get_settings(request: Request) -> Settings:
    """Return the Settings instance `create_app` bound to this app."""
    return request.app.state.settings


@lru_cache(maxsize=1)
def get_correction_prompt_builder() -> CorrectionPromptBuilder:
    return CorrectionPromptBuilder()


def get_structured_output_validator(request: Request) -> StructuredOutputValidator:
    """Return (and lazily cache) the validator bound to this app instance."""
    validator: StructuredOutputValidator | None = getattr(
        request.app.state,
        "structured_output_validator",
        None,
    )
    if validator is None:
        validator = StructuredOutputValidator(
            settings=get_settings(request),
            correction_prompt_builder=get_correction_prompt_builder(),
        )
        request.app.state.structured_output_validator = validator
    return validator


def get_intelligence_provider(request: Request) -> OllamaGemmaProvider:
    """Return (and lazily cache) the provider bound to this app instance."""
    provider: OllamaGemmaProvider | None = getattr(
        request.app.state,
        "intelligence_provider",
        None,
    )
    if provider is None:
        provider = OllamaGemmaProvider(
            settings=get_settings(request),
            validator=get_structured_output_validator(request),
        )
        request.app.state.intelligence_provider = provider
    return provider

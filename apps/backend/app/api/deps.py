"""Shared FastAPI dependencies."""

from functools import lru_cache

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env


@lru_cache(maxsize=1)
def get_correction_prompt_builder() -> CorrectionPromptBuilder:
    return CorrectionPromptBuilder()


@lru_cache(maxsize=1)
def get_structured_output_validator() -> StructuredOutputValidator:
    return StructuredOutputValidator(
        settings=get_settings(),
        correction_prompt_builder=get_correction_prompt_builder(),
    )


@lru_cache(maxsize=1)
def get_intelligence_provider() -> OllamaGemmaProvider:
    return OllamaGemmaProvider(
        settings=get_settings(),
        validator=get_structured_output_validator(),
    )

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
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
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


@lru_cache(maxsize=1)
def get_document_parser() -> DoclingDocumentParser:
    """Build a cached parser wired to `Settings.max_pdf_pages`.

    This factory is the integration seam that makes `MAX_PDF_PAGES` a real
    runtime knob — without it, operators who set the env var would still
    see the parser's internal default of 200. Docling's lazy import means
    constructing the parser here does not pull in Docling, so module load
    and unit-test startup stay cheap.
    """
    return DoclingDocumentParser(max_pdf_pages=get_settings().max_pdf_pages)

"""IntelligenceProvider: the LLM provider abstraction Protocol.

A concrete implementation (PDFX-E004-F002, `OllamaGemmaProvider`) is the only
file in the feature permitted to import the Ollama HTTP client. Every consumer
of structured LLM output — the extraction engine, the readiness probe — depends
on this Protocol so they can be unit-tested against fake providers without
running a real model.
"""

from typing import Any, Protocol, runtime_checkable

from app.features.extraction.intelligence.generation_result import GenerationResult


@runtime_checkable
class IntelligenceProvider(Protocol):
    async def generate(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> GenerationResult: ...

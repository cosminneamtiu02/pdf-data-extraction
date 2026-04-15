"""Provider-agnostic intelligence layer.

Defines the `IntelligenceProvider` Protocol, the `GenerationResult` carrier,
and the `StructuredOutputValidator` that compensates for LLMs lacking native
controlled generation by cleaning, parsing, validating, and retrying their raw
text output against a target JSONSchema. The concrete `OllamaGemmaProvider`
implementation lives in PDFX-E004-F002.
"""

from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.generation_result import GenerationResult
from app.features.extraction.intelligence.intelligence_provider import (
    IntelligenceProvider,
)
from app.features.extraction.intelligence.structured_output_validator import (
    StructuredOutputValidator,
)

__all__ = [
    "CorrectionPromptBuilder",
    "GenerationResult",
    "IntelligenceProvider",
    "StructuredOutputValidator",
]

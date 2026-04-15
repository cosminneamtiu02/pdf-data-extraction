"""Single source of truth for the LangExtract wrapper schema.

LangExtract's `model.infer` path returns a top-level wrapper of shape
`{"extractions": [...]}`, regardless of the skill's actual field schema.
Two code sites pass this wrapper schema to the StructuredOutputValidator:

    1. `OllamaGemmaProvider.infer` — when LangExtract's plugin path goes
       through the registered Ollama provider directly.
    2. `ExtractionEngine._ValidatingLangExtractAdapter.infer` — when the
       engine wraps the configured IntelligenceProvider before handing
       LangExtract a model.

Both must validate the same envelope shape, otherwise the two entry
paths run different validator contracts. Centralizing the schema here
makes drift impossible: the constant is defined once, and the import
cache guarantees both consumers see the same dict object by identity.
"""

from typing import Any

LANGEXTRACT_WRAPPER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"extractions": {"type": "array"}},
    "required": ["extractions"],
}

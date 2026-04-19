"""Pin the LangExtract wrapper schema's single source of truth.

LangExtract's `model.infer` path returns a top-level wrapper of shape
`{"extractions": [...]}`, regardless of the skill's actual field schema.
Two code sites pass this wrapper schema to the StructuredOutputValidator:

    1. `OllamaGemmaProvider.infer` — when LangExtract's plugin path goes
       through the registered Ollama provider directly.
    2. `_ValidatingLangExtractAdapter.infer` — the adapter class hosted
       in `app.features.extraction.extraction._validating_langextract_adapter`
       (split out of `extraction_engine.py` under issue #228) that the
       `ExtractionEngine` wraps the configured IntelligenceProvider in
       before handing LangExtract a model.

Previously each site declared its own `_LANGEXTRACT_WRAPPER_SCHEMA: dict`
constant. The comment said they had to match "byte-for-byte", which is
exactly the kind of invariant compilers cannot enforce — the two
definitions WERE able to drift, and there was no test pinning them
together. This file is the regression guard: the canonical definition
lives in `app.features.extraction.intelligence.langextract_wrapper_schema`,
and the constant is the same object (by Python identity) regardless of
which import path you take.
"""

from __future__ import annotations

from app.features.extraction.intelligence.langextract_wrapper_schema import (
    LANGEXTRACT_WRAPPER_SCHEMA,
)


def test_langextract_wrapper_schema_canonical_shape() -> None:
    expected = {
        "type": "object",
        "properties": {"extractions": {"type": "array"}},
        "required": ["extractions"],
    }
    assert expected == LANGEXTRACT_WRAPPER_SCHEMA


def test_langextract_wrapper_schema_is_shared_by_identity() -> None:
    """Both consumer modules must reference the same dict object.

    Importing the same module-level constant from two different
    consumers should yield the same object — Python guarantees this
    by module caching. Asserting identity (`is`) catches the
    regression where someone re-introduces a local copy.
    """
    from app.features.extraction.extraction import _validating_langextract_adapter
    from app.features.extraction.intelligence import ollama_gemma_provider

    assert _validating_langextract_adapter.LANGEXTRACT_WRAPPER_SCHEMA is LANGEXTRACT_WRAPPER_SCHEMA
    assert ollama_gemma_provider.LANGEXTRACT_WRAPPER_SCHEMA is LANGEXTRACT_WRAPPER_SCHEMA

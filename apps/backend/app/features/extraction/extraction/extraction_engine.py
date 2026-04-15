"""ExtractionEngine — thin async wrapper around `langextract.extract`.

This is the only file in the extraction feature permitted to import
`langextract`. The containment rule is enforced mechanically by the AST-scan
test `tests/unit/features/extraction/extraction/test_no_third_party_imports.py`
until the full `import-linter` contract arrives in PDFX-E007-F004.

The engine takes a `Skill`, a pre-concatenated document text, and an
`IntelligenceProvider`, and returns a list of `RawExtraction` — exactly
one per field declared in `skill.output_schema.properties`, in declared
order. Fields LangExtract reported but the skill did not declare are
dropped (hallucinations never leak downstream). Fields the skill declared
but LangExtract did not return are still emitted as placeholder
`RawExtraction(value=None, grounded=False)` rows so every declared field
is present in the output list — matching the project's "every declared
field always present" API-stability criterion. Duplicate field names
within LangExtract's output are deduped first-wins.

**StructuredOutputValidator routing.** LangExtract's orchestration calls
`model.infer(...)` directly, which would bypass the project's validator /
retry path (the fence-stripping + JSON-parse-with-retry loop that gives the
service its structured-output success rate). To keep that invariant intact,
the engine wraps the caller-supplied `IntelligenceProvider` in a private
`_ValidatingLangExtractAdapter(BaseLanguageModel)`. The adapter's `infer`
runs the entire batch under ONE `asyncio.run` (so every prompt shares one
event loop, matching providers that hold loop-bound connection pools) and
routes each prompt through `provider.generate(prompt, wrapper_schema)` —
which exercises `StructuredOutputValidator` — then yields the re-serialized
cleaned JSON text for LangExtract's resolver to parse. The wrapper schema
is not `skill.output_schema` (which describes extraction CONTENT and would
always fail against LangExtract's wrapper format) but the LangExtract
envelope shape: `{"type": "object", "required": ["extractions"],
"properties": {"extractions": {"type": "array"}}}`. That validates the
raw model text enough to retry on missing-wrapper failures without leaking
into field-level semantics.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

import langextract
from langextract.core.base_model import BaseLanguageModel
from langextract.core.data import AnnotatedDocument, ExampleData, Extraction
from langextract.core.types import ScoredOutput

from app.features.extraction.extraction.raw_extraction import RawExtraction

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from app.features.extraction.intelligence.intelligence_provider import IntelligenceProvider
    from app.features.extraction.skills.skill import Skill
    from app.features.extraction.skills.skill_example import SkillExample


# Schema for the validator call on the LangExtract path. The caller's real
# field-level schema (`skill.output_schema`) describes extraction CONTENT,
# not LangExtract's `{"extractions": [...]}` wrapper, so it cannot be used
# to validate raw model output directly. This schema enforces the wrapper
# shape — object with a top-level `extractions` ARRAY — which is what the
# Ollama/Gemma prompt asks for and what LangExtract's resolver expects to
# parse, without leaking into field-level semantics. The validator's
# fence-strip + JSON parse + retry loop runs on every call.
_LANGEXTRACT_WRAPPER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"extractions": {"type": "array"}},
    "required": ["extractions"],
}


class _ValidatingLangExtractAdapter(BaseLanguageModel):
    """BaseLanguageModel that routes each LangExtract call through the
    caller's `IntelligenceProvider.generate`, so the project's
    `StructuredOutputValidator` (cleanup + retry) runs on every model call.

    Lives inside `extraction_engine.py` so LangExtract containment stays
    at one file. Not a public export.
    """

    def __init__(self, inner: IntelligenceProvider) -> None:
        super().__init__()
        self._inner = inner

    def infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,  # noqa: ARG002 - LangExtract passes orchestrator kwargs that we do not consume
    ) -> Iterator[Sequence[ScoredOutput]]:
        # Run the entire batch under ONE `asyncio.run` so every prompt
        # shares one event loop. Per-prompt `asyncio.run` would create a
        # fresh loop each time, which breaks providers like
        # `OllamaGemmaProvider` that hold a loop-bound `httpx.AsyncClient`
        # connection pool: the second prompt would hit a client whose pool
        # is bound to a closed loop. See the sync/async-bridge note in
        # `ollama_gemma_provider.py`.
        results = asyncio.run(self._generate_batch(list(batch_prompts)))
        for data in results:
            yield [ScoredOutput(score=1.0, output=json.dumps(data))]

    async def _generate_batch(self, prompts: list[str]) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for prompt in prompts:
            result = await self._inner.generate(prompt, _LANGEXTRACT_WRAPPER_SCHEMA)
            outputs.append(result.data)
        return outputs


class ExtractionEngine:
    """Constructs LangExtract call parameters from a Skill and invokes it."""

    async def extract(
        self,
        concatenated_text: str,
        skill: Skill,
        provider: IntelligenceProvider,
    ) -> list[RawExtraction]:
        """Run LangExtract against `concatenated_text` using `skill` + `provider`.

        Returns one `RawExtraction` per distinct field name declared in
        `skill.output_schema`. Empty input text short-circuits to an empty
        result without touching the provider.
        """
        if not concatenated_text:
            return []

        declared_fields = _declared_field_names(skill)
        if not declared_fields:
            # No declared fields means no API contract to honor — short-
            # circuit rather than inviting hallucinations through. A skill
            # with zero output fields is a skill-authoring error caught
            # upstream by `SkillYamlSchema` validation, but the engine
            # stays strict here as defense in depth.
            return []

        examples = self._build_examples(skill.examples)
        adapter = _ValidatingLangExtractAdapter(provider)

        result = await asyncio.to_thread(
            self._invoke_langextract,
            concatenated_text,
            skill.prompt,
            examples,
            adapter,
        )

        return self._to_raw_extractions(result, declared_fields)

    @staticmethod
    def _invoke_langextract(
        text: str,
        prompt: str,
        examples: list[ExampleData],
        model: BaseLanguageModel,
    ) -> AnnotatedDocument:
        result: Any = langextract.extract(
            text_or_documents=text,
            prompt_description=prompt,
            examples=examples,
            model=model,
            max_workers=1,
            batch_length=1,
            show_progress=False,
            fetch_urls=False,
        )
        # LangExtract returns `AnnotatedDocument | list[AnnotatedDocument]`;
        # single-string input always produces a single `AnnotatedDocument`.
        if isinstance(result, list):
            if not result:
                return AnnotatedDocument(extractions=[], text=text)
            return cast("AnnotatedDocument", result[0])
        return cast("AnnotatedDocument", result)

    @staticmethod
    def _build_examples(skill_examples: tuple[SkillExample, ...]) -> list[ExampleData]:
        built: list[ExampleData] = []
        for example in skill_examples:
            extractions = [
                Extraction(
                    extraction_class=field_name,
                    extraction_text=str(value),
                )
                for field_name, value in example.output.items()
            ]
            built.append(ExampleData(text=example.input, extractions=extractions))
        return built

    @staticmethod
    def _to_raw_extractions(
        result: AnnotatedDocument,
        declared_fields: tuple[str, ...],
    ) -> list[RawExtraction]:
        """Map LangExtract's native `Extraction` list to `RawExtraction`.

        Invariants enforced here (from PDFX-E004-F003 AC + the project's
        "every declared field always present" API-stability rule):

        - Output order matches `declared_fields` exactly.
        - Fields NOT declared in `skill.output_schema.properties` are
          dropped (hallucinated / extra fields never leak downstream).
        - Fields declared but MISSING from LangExtract's output appear as
          placeholder `RawExtraction(value=None, grounded=False)` rows so
          downstream assembly can always look them up by field name.
        - Duplicate field names within LangExtract's output are deduped
          first-wins.
        """
        extractions = result.extractions or []
        by_field: dict[str, Extraction] = {}
        for extraction in extractions:
            field_name = extraction.extraction_class
            if field_name not in declared_fields:
                continue
            if field_name in by_field:
                continue
            by_field[field_name] = extraction

        output: list[RawExtraction] = []
        for field_name in declared_fields:
            extraction = by_field.get(field_name)
            if extraction is None:
                # Declared but missing — emit a placeholder so callers can
                # still look this field up by name and see status=missing.
                output.append(
                    RawExtraction(
                        field_name=field_name,
                        value=None,
                        char_offset_start=None,
                        char_offset_end=None,
                        grounded=False,
                        attempts=1,
                    ),
                )
                continue

            interval = extraction.char_interval
            start = interval.start_pos if interval is not None else None
            end = interval.end_pos if interval is not None else None
            grounded = start is not None and end is not None

            output.append(
                RawExtraction(
                    field_name=field_name,
                    value=extraction.extraction_text,
                    char_offset_start=start,
                    char_offset_end=end,
                    grounded=grounded,
                    attempts=1,
                ),
            )
        return output


def _declared_field_names(skill: Skill) -> tuple[str, ...]:
    """Names of fields declared in the skill's JSONSchema `properties`.

    Returns an empty tuple if the skill schema has no `properties` block —
    which `extract` uses as a "strict-empty" signal to return no results
    rather than letting LangExtract output pass through unfiltered. Order
    of insertion into the JSONSchema is preserved (Python dicts are
    ordered) and becomes the output order of `RawExtraction`.
    """
    properties_obj: Any = skill.output_schema.get("properties")
    if not isinstance(properties_obj, dict):
        return ()
    return tuple(str(name) for name in cast("dict[Any, Any]", properties_obj))

"""ExtractionEngine — thin async wrapper around `langextract.extract`.

This is the only file in the extraction feature permitted to import
`langextract`. The containment rule is enforced mechanically by the AST-scan
test `tests/unit/features/extraction/extraction/test_no_third_party_imports.py`
until the full `import-linter` contract arrives in PDFX-E007-F004.

The engine takes a `Skill`, a pre-concatenated document text, and an
`IntelligenceProvider`, and returns a list of `RawExtraction` — one per
field DECLARED in `skill.output_schema`, deduped first-wins across
LangExtract chunks. Hallucinated field names not present in the skill are
dropped so downstream `SpanResolver` / response assembly never see fields
the skill did not declare.

**StructuredOutputValidator routing.** LangExtract's orchestration calls
`model.infer(...)` directly, which would bypass the project's validator /
retry path (the fence-stripping + JSON-parse-with-retry loop that gives the
service its structured-output success rate). To keep that invariant intact,
the engine wraps the caller-supplied `IntelligenceProvider` in a private
`_ValidatingLangExtractAdapter(BaseLanguageModel)`. The adapter's `infer`
calls `provider.generate(prompt, permissive_schema)` per prompt — which
routes through `StructuredOutputValidator` — and yields the re-serialized
cleaned JSON text for LangExtract's resolver to parse. The "permissive
schema" (`{"type": "object"}`) is deliberate: field-level JSONSchema lives
in `skill.output_schema` but targets extraction CONTENT, not LangExtract's
`{"extractions":[...]}` wrapper format. Using the skill schema here would
always fail. The permissive schema exercises the validator's cleanup and
retry-on-parse-error behavior without false negatives on the wrapper shape.
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


# Permissive schema used for the LangExtract-path validator call. The caller's
# real field-level schema lives on `skill.output_schema` but describes
# extraction CONTENT, not LangExtract's wrapper shape — it cannot be used
# directly on raw model output. This schema still engages the validator's
# cleanup + JSON-parse + retry loop (which is what we want) while accepting
# any JSON object as structurally valid (which is what LangExtract needs).
_PERMISSIVE_OBJECT_SCHEMA: dict[str, Any] = {"type": "object"}


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
        for prompt in batch_prompts:
            result = asyncio.run(
                self._inner.generate(prompt, _PERMISSIVE_OBJECT_SCHEMA),
            )
            yield [ScoredOutput(score=1.0, output=json.dumps(result.data))]


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

        examples = self._build_examples(skill.examples)
        allowed_fields = _declared_field_names(skill)
        adapter = _ValidatingLangExtractAdapter(provider)

        result = await asyncio.to_thread(
            self._invoke_langextract,
            concatenated_text,
            skill.prompt,
            examples,
            adapter,
        )

        return self._to_raw_extractions(result, allowed_fields)

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
        allowed_fields: frozenset[str],
    ) -> list[RawExtraction]:
        extractions = result.extractions or []
        seen: set[str] = set()
        output: list[RawExtraction] = []
        for extraction in extractions:
            field_name = extraction.extraction_class
            if allowed_fields and field_name not in allowed_fields:
                # Hallucinated / extra field not declared in skill.output_schema:
                # drop it so downstream consumers only ever see declared fields.
                continue
            if field_name in seen:
                continue
            seen.add(field_name)

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


def _declared_field_names(skill: Skill) -> frozenset[str]:
    """Names of fields declared in the skill's JSONSchema `properties`.

    Returns an empty frozenset if the skill schema has no `properties` block,
    which disables filtering (the engine accepts everything LangExtract
    returned — used only as a defensive fallback for skills authored without
    a structured properties block).
    """
    properties_obj: Any = skill.output_schema.get("properties")
    if not isinstance(properties_obj, dict):
        return frozenset()
    keys: list[str] = [str(name) for name in cast("dict[Any, Any]", properties_obj)]
    return frozenset(keys)

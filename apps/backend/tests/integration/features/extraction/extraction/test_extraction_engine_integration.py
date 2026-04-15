"""Integration tests for ExtractionEngine against the real `langextract` library.

These tests wire the engine to a real `langextract.extract` call with a
`FakeLanguageModel` that returns canned fenced-JSON responses — LangExtract's
own chunking, source grounding, and result aggregation run for real. The
only piece faked is the underlying LLM call itself (so we don't need a live
Ollama + Gemma setup in CI).

This covers the three integration scenarios in PDFX-E004-F003:

- `Skill → LangExtract → provider` parameter wiring carries skill data.
- Returned character offsets slice the input text to contain the value.
- Field names match the skill's `output_schema.properties` keys.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

from langextract.core.base_model import BaseLanguageModel
from langextract.core.types import ScoredOutput

from app.features.extraction.extraction.extraction_engine import ExtractionEngine
from app.features.extraction.intelligence.generation_result import GenerationResult
from app.features.extraction.skills.skill import Skill
from app.features.extraction.skills.skill_example import SkillExample


class _FakeModel(BaseLanguageModel):
    """Minimal dual-interface provider double.

    Satisfies LangExtract's `BaseLanguageModel.infer` abstract method by
    returning a fenced JSON string with one extraction per declared field.
    Also implements the internal `generate` method so it conforms to the
    `IntelligenceProvider` protocol for type-checking, even though the
    LangExtract path calls `infer`, not `generate`.
    """

    def __init__(self, canned_output: str) -> None:
        super().__init__()
        self._canned = canned_output
        self.received_prompts: list[str] = []

    def infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,
    ) -> Iterator[Sequence[ScoredOutput]]:
        _ = kwargs
        for prompt in batch_prompts:
            self.received_prompts.append(prompt)
            yield [ScoredOutput(score=1.0, output=self._canned)]

    async def generate(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> GenerationResult:
        _ = prompt
        _ = output_schema
        return GenerationResult(data={}, attempts=1, raw_output=self._canned)


def _skill(field_names: tuple[str, ...]) -> Skill:
    output_schema = {
        "type": "object",
        "required": list(field_names),
        "properties": {name: {"type": "string"} for name in field_names},
    }
    examples = (
        SkillExample(
            input="Bob is 25 and lives in London.",
            output={name: f"demo-{name}" for name in field_names},
        ),
    )
    return Skill(
        name="integration-skill",
        version=1,
        description=None,
        prompt="Extract biographical fields from the text.",
        examples=examples,
        output_schema=MappingProxyType(output_schema),
    )


async def test_engine_wires_real_langextract_with_three_grounded_fields() -> None:
    text = "Alice is 30 years old and lives in Paris."
    canned = (
        "```json\n"
        '{"extractions":['
        '{"name":"Alice","name_attributes":{}},'
        '{"age":"30","age_attributes":{}},'
        '{"city":"Paris","city_attributes":{}}'
        "]}\n"
        "```"
    )
    skill = _skill(("name", "age", "city"))

    results = await ExtractionEngine().extract(text, skill, _FakeModel(canned))

    assert len(results) == 3
    returned_field_names = {r.field_name for r in results}
    assert returned_field_names == {"name", "age", "city"}


async def test_engine_returns_offsets_that_slice_input_text() -> None:
    text = "Alice is 30 years old and lives in Paris."
    canned = (
        '```json\n{"extractions":['
        '{"name":"Alice","name_attributes":{}},'
        '{"city":"Paris","city_attributes":{}}'
        "]}\n```"
    )
    skill = _skill(("name", "city"))

    results = await ExtractionEngine().extract(text, skill, _FakeModel(canned))

    by_name = {r.field_name: r for r in results}
    name_extraction = by_name["name"]
    city_extraction = by_name["city"]

    assert name_extraction.grounded is True
    assert name_extraction.char_offset_start is not None
    assert name_extraction.char_offset_end is not None
    assert text[name_extraction.char_offset_start : name_extraction.char_offset_end] == "Alice"

    assert city_extraction.grounded is True
    assert city_extraction.char_offset_start is not None
    assert city_extraction.char_offset_end is not None
    assert text[city_extraction.char_offset_start : city_extraction.char_offset_end] == "Paris"


async def test_engine_passes_skill_prompt_through_to_model() -> None:
    text = "Alice is 30."
    canned = '```json\n{"extractions":[{"name":"Alice","name_attributes":{}}]}\n```'
    skill = _skill(("name",))
    model = _FakeModel(canned)

    await ExtractionEngine().extract(text, skill, model)

    assert model.received_prompts, "FakeModel.infer was never called"
    joined = "\n".join(model.received_prompts)
    assert skill.prompt in joined

"""Integration tests for ExtractionEngine against the real `langextract` library.

These tests wire the engine to a real `langextract.extract` call. The
`_FakeProvider` below only implements `IntelligenceProvider.generate`; the
engine wraps it in its own `_ValidatingLangExtractAdapter` internally, and
LangExtract's real chunking, source-grounding, and aggregation run for real.
The only thing faked is the underlying LLM call — no live Ollama + Gemma
required.

Covers the three integration scenarios from PDFX-E004-F003:

- `Skill → LangExtract → provider.generate` parameter wiring carries skill data.
- Returned character offsets slice the input text to contain the value.
- Field names match the skill's `output_schema.properties` keys.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from app.features.extraction.extraction.extraction_engine import ExtractionEngine
from app.features.extraction.intelligence.generation_result import GenerationResult
from app.features.extraction.skills.skill import Skill
from app.features.extraction.skills.skill_example import SkillExample


class _FakeProvider:
    """Minimal IntelligenceProvider double.

    Returns canned parsed extraction payloads (already validator-clean) from
    `generate` — the engine's internal adapter json.dumps the data back into
    text for LangExtract's resolver to parse. The `output_schema` parameter
    is ignored here because the engine always passes the permissive
    `{"type": "object"}` schema on the LangExtract path.
    """

    def __init__(self, canned_data: dict[str, Any]) -> None:
        self._canned = canned_data
        self.received_prompts: list[str] = []

    async def generate(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> GenerationResult:
        _ = output_schema
        self.received_prompts.append(prompt)
        return GenerationResult(
            data=self._canned,
            attempts=1,
            raw_output=str(self._canned),
        )


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
    canned = {
        "extractions": [
            {"name": "Alice", "name_attributes": {}},
            {"age": "30", "age_attributes": {}},
            {"city": "Paris", "city_attributes": {}},
        ],
    }
    skill = _skill(("name", "age", "city"))

    results = await ExtractionEngine().extract(text, skill, _FakeProvider(canned))

    assert len(results) == 3
    assert {r.field_name for r in results} == {"name", "age", "city"}


async def test_engine_returns_offsets_that_slice_input_text() -> None:
    text = "Alice is 30 years old and lives in Paris."
    canned = {
        "extractions": [
            {"name": "Alice", "name_attributes": {}},
            {"city": "Paris", "city_attributes": {}},
        ],
    }
    skill = _skill(("name", "city"))

    results = await ExtractionEngine().extract(text, skill, _FakeProvider(canned))

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


async def test_engine_passes_skill_prompt_through_to_provider_generate() -> None:
    text = "Alice is 30."
    canned = {"extractions": [{"name": "Alice", "name_attributes": {}}]}
    skill = _skill(("name",))
    provider = _FakeProvider(canned)

    await ExtractionEngine().extract(text, skill, provider)

    assert provider.received_prompts, "_FakeProvider.generate was never called"
    joined = "\n".join(provider.received_prompts)
    assert skill.prompt in joined


async def test_engine_drops_extracted_fields_not_in_schema() -> None:
    """A hallucinated field returned by LangExtract's resolver must be
    filtered out by the engine, since it is not declared in the skill.
    """

    text = "Alice is 30 years old."
    canned = {
        "extractions": [
            {"name": "Alice", "name_attributes": {}},
            {"hallucinated": "bogus", "hallucinated_attributes": {}},
        ],
    }
    skill = _skill(("name",))  # `hallucinated` is NOT declared

    results = await ExtractionEngine().extract(text, skill, _FakeProvider(canned))

    assert [r.field_name for r in results] == ["name"]


async def test_engine_emits_placeholder_for_missing_declared_field() -> None:
    """Declared fields missing from LangExtract's output must still appear
    as placeholder RawExtraction rows so every declared field is present.
    """

    text = "Alice is 30 years old and lives in Paris."
    canned = {
        "extractions": [{"name": "Alice", "name_attributes": {}}],
    }
    skill = _skill(("name", "age", "city"))

    results = await ExtractionEngine().extract(text, skill, _FakeProvider(canned))

    assert [r.field_name for r in results] == ["name", "age", "city"]
    by_field = {r.field_name: r for r in results}
    assert by_field["age"].value is None
    assert by_field["age"].grounded is False
    assert by_field["city"].value is None
    assert by_field["city"].grounded is False

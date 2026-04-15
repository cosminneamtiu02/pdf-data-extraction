"""Unit tests for ExtractionEngine.

The engine is a thin wrapper around `langextract.extract`. Unit tests patch
the module-level `langextract.extract` attribute on the engine module and
verify the transformation from LangExtract's native `AnnotatedDocument` /
`Extraction` shape into feature-owned `RawExtraction` instances, plus the
output-schema filtering and the validating LangExtract adapter that routes
every call through `IntelligenceProvider.generate`. End-to-end integration
with real LangExtract is covered in
`tests/integration/features/extraction/extraction/test_extraction_engine_integration.py`.
"""

from __future__ import annotations

import inspect
import json
from types import MappingProxyType
from typing import Any

import pytest
from langextract.core.data import AnnotatedDocument, CharInterval, Extraction

from app.features.extraction.extraction import extraction_engine as engine_module
from app.features.extraction.extraction.extraction_engine import (
    ExtractionEngine,
    _ValidatingLangExtractAdapter,
)
from app.features.extraction.intelligence.generation_result import GenerationResult
from app.features.extraction.skills.skill import Skill
from app.features.extraction.skills.skill_example import SkillExample


class _FakeProvider:
    """Minimal IntelligenceProvider double.

    `langextract.extract` is monkeypatched away in these unit tests, so
    `generate` is not actually invoked by the engine — it only needs to
    exist so the adapter can be constructed.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def generate(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> GenerationResult:
        self.calls.append((prompt, output_schema))
        return GenerationResult(data={"ok": True}, attempts=1, raw_output='{"ok": true}')


def _build_skill(field_names: tuple[str, ...]) -> Skill:
    properties = {name: {"type": "string"} for name in field_names}
    output_schema = {
        "type": "object",
        "required": list(field_names),
        "properties": properties,
    }
    examples = (
        SkillExample(
            input="Alice lives in Paris.",
            output={name: f"example-{name}" for name in field_names},
        ),
    )
    return Skill(
        name="test-skill",
        version=1,
        description=None,
        prompt="Extract the following fields.",
        examples=examples,
        output_schema=MappingProxyType(output_schema),
    )


def _extraction(
    field_name: str,
    value: str,
    start: int | None,
    end: int | None,
) -> Extraction:
    interval = CharInterval(start_pos=start, end_pos=end) if start is not None else None
    return Extraction(
        extraction_class=field_name,
        extraction_text=value,
        char_interval=interval,
    )


@pytest.fixture
def patch_extract(monkeypatch: pytest.MonkeyPatch):
    """Patch `langextract.extract` as seen by the engine module."""

    calls: list[dict[str, Any]] = []
    canned: dict[str, AnnotatedDocument] = {}

    def _fake_extract(**kwargs: Any) -> AnnotatedDocument:
        calls.append(kwargs)
        text = kwargs["text_or_documents"]
        return canned.get("result", AnnotatedDocument(extractions=[], text=text))

    monkeypatch.setattr(engine_module.langextract, "extract", _fake_extract)

    def _install(result: AnnotatedDocument) -> None:
        canned["result"] = result

    _install.calls = calls  # type: ignore[attr-defined]
    return _install


async def test_extract_with_three_grounded_fields_returns_one_raw_extraction_per_field(
    patch_extract: Any,
) -> None:
    text = "Alice is 30 years old and lives in Paris."
    skill = _build_skill(("name", "age", "city"))
    annotated = AnnotatedDocument(
        extractions=[
            _extraction("name", "Alice", 0, 5),
            _extraction("age", "30", 9, 11),
            _extraction("city", "Paris", 35, 40),
        ],
        text=text,
    )
    patch_extract(annotated)

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert len(results) == 3
    assert [r.field_name for r in results] == ["name", "age", "city"]
    for raw in results:
        assert raw.value
        assert raw.grounded is True
        assert raw.char_offset_start is not None
        assert raw.char_offset_end is not None
        assert 0 <= raw.char_offset_start < raw.char_offset_end <= len(text)
        assert raw.attempts == 1


async def test_extract_with_ungrounded_inference_sets_offsets_none_and_grounded_false(
    patch_extract: Any,
) -> None:
    text = "Alice is 30 years old."
    skill = _build_skill(("nationality",))
    annotated = AnnotatedDocument(
        extractions=[_extraction("nationality", "French", None, None)],
        text=text,
    )
    patch_extract(annotated)

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert len(results) == 1
    raw = results[0]
    assert raw.field_name == "nationality"
    assert raw.value == "French"
    assert raw.char_offset_start is None
    assert raw.char_offset_end is None
    assert raw.grounded is False


async def test_extract_attempts_field_is_one(patch_extract: Any) -> None:
    text = "hello"
    skill = _build_skill(("name",))
    patch_extract(
        AnnotatedDocument(extractions=[_extraction("name", "Alice", 0, 5)], text=text),
    )

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert results[0].attempts == 1


async def test_extract_dedupes_duplicate_field_names_keeping_first(patch_extract: Any) -> None:
    text = "Alice in one place, and also Alicia in another place."
    skill = _build_skill(("name",))
    annotated = AnnotatedDocument(
        extractions=[
            _extraction("name", "Alice", 0, 5),
            _extraction("name", "Alicia", 29, 35),
        ],
        text=text,
    )
    patch_extract(annotated)

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert len(results) == 1
    assert results[0].value == "Alice"
    assert results[0].char_offset_start == 0


async def test_extract_drops_fields_not_declared_in_output_schema(
    patch_extract: Any,
) -> None:
    """Hallucinated field names (fields not declared in the skill's
    `output_schema.properties`) must be filtered out so downstream
    consumers only ever see fields declared in the skill.
    """

    text = "Alice is 30."
    skill = _build_skill(("name", "age"))  # no `hallucinated` in the schema
    annotated = AnnotatedDocument(
        extractions=[
            _extraction("name", "Alice", 0, 5),
            _extraction("hallucinated", "bogus", None, None),
            _extraction("age", "30", 9, 11),
        ],
        text=text,
    )
    patch_extract(annotated)

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert [r.field_name for r in results] == ["name", "age"]


async def test_extract_emits_placeholder_for_declared_field_missing_from_output(
    patch_extract: Any,
) -> None:
    """Every declared field must appear in the result exactly once, even
    when LangExtract did not return it. Missing fields become placeholder
    RawExtraction rows with value=None and grounded=False so downstream
    assembly can always look them up by name (API-stability guarantee).
    """

    text = "Alice is 30."
    skill = _build_skill(("name", "age", "city"))
    annotated = AnnotatedDocument(
        extractions=[_extraction("name", "Alice", 0, 5)],
        text=text,
    )
    patch_extract(annotated)

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert [r.field_name for r in results] == ["name", "age", "city"]
    name_row, age_row, city_row = results
    assert name_row.value == "Alice"
    assert name_row.grounded is True
    assert age_row.value is None
    assert age_row.char_offset_start is None
    assert age_row.char_offset_end is None
    assert age_row.grounded is False
    assert city_row.value is None
    assert city_row.grounded is False


async def test_extract_returns_results_in_declared_schema_order(
    patch_extract: Any,
) -> None:
    """Output order must follow the skill's `output_schema.properties`
    insertion order, not LangExtract's return order.
    """

    text = "..."
    skill = _build_skill(("city", "age", "name"))  # intentional non-alpha order
    annotated = AnnotatedDocument(
        extractions=[
            _extraction("name", "Alice", 0, 5),
            _extraction("age", "30", 9, 11),
            _extraction("city", "Paris", 35, 40),
        ],
        text=text,
    )
    patch_extract(annotated)

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert [r.field_name for r in results] == ["city", "age", "name"]


async def test_extract_works_with_skill_from_schema_using_mappingproxy(
    patch_extract: Any,
) -> None:
    """Real skills loaded via `Skill.from_schema` wrap `output_schema` and
    its nested `properties` in `MappingProxyType`. The engine must treat
    those as Mappings, not require a plain `dict`, otherwise declared
    fields would come back empty and every real extraction would short-
    circuit to `[]`.
    """

    from app.features.extraction.skills.skill_yaml_schema import SkillYamlSchema

    schema = SkillYamlSchema(
        name="mappingproxy-skill",
        version=1,
        description=None,
        prompt="Extract name and age.",
        examples=[
            SkillExample(input="Alice is 30.", output={"name": "Alice", "age": "30"}),
        ],
        output_schema={
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "string"},
            },
        },
    )
    skill = Skill.from_schema(schema)

    text = "Alice is 30 years old."
    patch_extract(
        AnnotatedDocument(
            extractions=[
                _extraction("name", "Alice", 0, 5),
                _extraction("age", "30", 9, 11),
            ],
            text=text,
        ),
    )

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert [r.field_name for r in results] == ["name", "age"]
    assert results[0].value == "Alice"
    assert results[1].value == "30"


async def test_extract_with_zero_declared_fields_returns_empty_without_calling_langextract(
    patch_extract: Any,
) -> None:
    """A skill whose output_schema has no `properties` (degenerate case)
    must not invite hallucinated fields through. Engine short-circuits.
    """

    schema_no_properties: dict[str, Any] = {"type": "object"}
    skill = Skill(
        name="empty-skill",
        version=1,
        description=None,
        prompt="do nothing",
        examples=(SkillExample(input="x", output={}),),
        output_schema=MappingProxyType(schema_no_properties),
    )

    results = await ExtractionEngine().extract("some text", skill, _FakeProvider())

    assert results == []
    assert patch_extract.calls == []  # type: ignore[attr-defined]


async def test_extract_with_empty_text_returns_empty_list_without_invoking_langextract(
    patch_extract: Any,
) -> None:
    skill = _build_skill(("name",))

    results = await ExtractionEngine().extract("", skill, _FakeProvider())

    assert results == []
    assert patch_extract.calls == []  # type: ignore[attr-defined]


async def test_extract_with_no_langextract_output_returns_placeholders(
    patch_extract: Any,
) -> None:
    """When LangExtract returns nothing but the skill declares fields, the
    engine emits one placeholder RawExtraction per declared field (value
    None, grounded False) — the "every declared field always present"
    invariant.
    """

    skill = _build_skill(("name", "age"))
    patch_extract(AnnotatedDocument(extractions=[], text="hello"))

    results = await ExtractionEngine().extract("hello", skill, _FakeProvider())

    assert [r.field_name for r in results] == ["name", "age"]
    assert all(r.value is None for r in results)
    assert all(r.grounded is False for r in results)


async def test_extract_with_list_return_type_uses_first_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LangExtract sometimes returns `list[AnnotatedDocument]` — take the first."""

    text = "hello Alice"
    skill = _build_skill(("name",))

    first = AnnotatedDocument(
        extractions=[_extraction("name", "Alice", 6, 11)],
        text=text,
    )

    def _fake_extract(**kwargs: Any) -> list[AnnotatedDocument]:
        _ = kwargs
        return [first]

    monkeypatch.setattr(engine_module.langextract, "extract", _fake_extract)

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert len(results) == 1
    assert results[0].field_name == "name"
    assert results[0].value == "Alice"


async def test_extract_propagates_provider_exception_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine is a thin wrapper — errors from LangExtract must propagate
    out of `extract` unchanged. Error mapping to DomainError is PDFX-E004-F004.
    """

    def _fake_extract(**kwargs: Any) -> AnnotatedDocument:
        _ = kwargs
        msg = "provider blew up"
        raise RuntimeError(msg)

    monkeypatch.setattr(engine_module.langextract, "extract", _fake_extract)
    skill = _build_skill(("name",))

    with pytest.raises(RuntimeError, match="provider blew up"):
        await ExtractionEngine().extract("hello", skill, _FakeProvider())


async def test_extract_passes_skill_prompt_and_examples_to_langextract(
    patch_extract: Any,
) -> None:
    text = "hello"
    skill = _build_skill(("name",))
    patch_extract(AnnotatedDocument(extractions=[], text=text))

    await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert len(patch_extract.calls) == 1  # type: ignore[attr-defined]
    call_kwargs = patch_extract.calls[0]  # type: ignore[attr-defined]
    assert call_kwargs["prompt_description"] == skill.prompt
    assert call_kwargs["text_or_documents"] == text
    assert call_kwargs["max_workers"] == 1
    assert call_kwargs["fetch_urls"] is False
    examples = call_kwargs["examples"]
    assert len(examples) == 1
    assert examples[0].text == "Alice lives in Paris."
    assert len(examples[0].extractions) == 1
    assert examples[0].extractions[0].extraction_class == "name"
    assert examples[0].extractions[0].extraction_text == "example-name"


async def test_extract_wraps_provider_in_validating_adapter(
    patch_extract: Any,
) -> None:
    """The model passed to `langextract.extract` must be the engine's internal
    `_ValidatingLangExtractAdapter`, not the caller's raw provider — that is
    how the StructuredOutputValidator routing is guaranteed on every call.
    """

    skill = _build_skill(("name",))
    patch_extract(AnnotatedDocument(extractions=[], text="hello"))
    provider = _FakeProvider()

    await ExtractionEngine().extract("hello", skill, provider)

    call_kwargs = patch_extract.calls[0]  # type: ignore[attr-defined]
    model = call_kwargs["model"]
    assert isinstance(model, _ValidatingLangExtractAdapter)


async def test_validating_adapter_infer_routes_through_provider_generate() -> None:
    """The adapter's `infer` must call `inner.generate(prompt, wrapper_schema)`
    per prompt via `run_coroutine_threadsafe(..., main_loop)` and yield
    one `ScoredOutput` per call carrying the JSON-serialized generate-
    result data. Run the adapter from a worker thread via `asyncio.to_thread`
    so the bridge path matches how the engine invokes LangExtract.
    """
    import asyncio as _asyncio

    provider = _FakeProvider()
    main_loop = _asyncio.get_running_loop()
    adapter = _ValidatingLangExtractAdapter(provider, main_loop)

    def _run_infer() -> list[list[Any]]:
        return [list(batch) for batch in adapter.infer(["prompt-a", "prompt-b"])]

    results = await _asyncio.to_thread(_run_infer)

    assert len(provider.calls) == 2
    assert provider.calls[0][0] == "prompt-a"
    assert provider.calls[1][0] == "prompt-b"
    # Wrapper schema: enforces {"extractions": [...]} envelope shape on the
    # raw model text so the validator retries on missing-wrapper failures,
    # without conflating skill.output_schema (which describes CONTENT).
    wrapper_schema = provider.calls[0][1]
    assert wrapper_schema["type"] == "object"
    assert wrapper_schema["required"] == ["extractions"]
    assert wrapper_schema["properties"]["extractions"]["type"] == "array"
    assert len(results) == 2
    assert len(results[0]) == 1
    assert results[0][0].output is not None
    assert json.loads(results[0][0].output) == {"ok": True}


async def test_validating_adapter_routes_generate_back_to_main_event_loop() -> None:
    """Every `provider.generate` coroutine scheduled from LangExtract's
    sync `infer` path must execute on the application's main event loop
    (captured in `ExtractionEngine.extract`). `OllamaGemmaProvider`'s
    shared `httpx.AsyncClient` connection pool is bound to that loop, so
    running the coroutine on a different loop would break on the second
    prompt with a "Future attached to a different loop" error. Assert
    every prompt's coroutine runs on the captured main loop.
    """
    import asyncio as _asyncio

    captured_loops: list[int] = []
    main_loop = _asyncio.get_running_loop()
    expected_loop_id = id(main_loop)

    class _LoopCapturingProvider:
        async def generate(
            self,
            prompt: str,
            output_schema: dict[str, Any],
        ) -> GenerationResult:
            _ = prompt
            _ = output_schema
            captured_loops.append(id(_asyncio.get_running_loop()))
            return GenerationResult(
                data={"ok": True},
                attempts=1,
                raw_output='{"ok": true}',
            )

    adapter = _ValidatingLangExtractAdapter(_LoopCapturingProvider(), main_loop)

    def _run_infer() -> None:
        list(adapter.infer(["p1", "p2", "p3"]))

    await _asyncio.to_thread(_run_infer)

    assert len(captured_loops) == 3
    assert all(lid == expected_loop_id for lid in captured_loops), (
        f"expected all prompts on main loop {expected_loop_id}, got {captured_loops}"
    )


def test_extract_method_is_async() -> None:
    assert inspect.iscoroutinefunction(ExtractionEngine.extract)


def test_raw_extraction_type_does_not_import_langextract() -> None:
    """Importing `raw_extraction` alone must not pull LangExtract into `sys.modules`."""

    import subprocess
    import sys

    code = (
        "import sys\n"
        "import app.features.extraction.extraction.raw_extraction  # noqa: F401\n"
        "assert 'langextract' not in sys.modules, "
        "sorted(k for k in sys.modules if 'langextract' in k)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

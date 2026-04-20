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
    declared_field_names,
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


async def test_extract_propagates_validator_attempts_from_provider_to_raw_extractions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine must surface the validator's retry count on every
    ``RawExtraction`` it produces, so downstream ``ExtractionMetadata``
    sees the real ``attempts_per_field`` (issue #135). Before the fix,
    ``_to_raw_extractions`` hardcoded ``attempts=1`` regardless of how
    many retries ``StructuredOutputValidator`` actually performed.

    We stub ``langextract.extract`` so the adapter's ``infer`` drives
    ``provider.generate`` once per prompt — then the adapter records
    the resulting ``GenerationResult.attempts`` and the engine attaches
    that count to every declared field (both real and placeholder rows).
    """

    class _RetryingProvider:
        """Simulates the validator path: 3 attempts taken on this prompt."""

        async def generate(
            self,
            prompt: str,
            output_schema: dict[str, Any],
        ) -> GenerationResult:
            _ = prompt
            _ = output_schema
            return GenerationResult(
                data={"extractions": []},
                attempts=3,
                raw_output='{"extractions": []}',
            )

    # Stub langextract.extract so the real LangExtract resolver isn't invoked
    # but the adapter.infer path IS — that's where the attempts count is
    # captured. We still need to exercise `model.infer(...)` at least once
    # so the adapter observes a GenerationResult.
    def _fake_extract(**kwargs: Any) -> AnnotatedDocument:
        model = kwargs["model"]
        # Drive the adapter's infer path ONCE so the adapter records
        # the provider's attempts count — mirroring how LangExtract itself
        # calls `model.infer([prompt])` during normal extraction.
        list(model.infer(["dummy-prompt"]))
        # Then return an AnnotatedDocument with ONE real field plus the
        # other two declared fields missing — so the engine emits two
        # placeholder rows and one real row. Both kinds must carry the
        # propagated attempts count.
        return AnnotatedDocument(
            extractions=[_extraction("name", "Alice", 0, 5)],
            text=kwargs["text_or_documents"],
        )

    monkeypatch.setattr(engine_module.langextract, "extract", _fake_extract)

    text = "Alice lives somewhere."
    skill = _build_skill(("name", "age", "city"))

    results = await ExtractionEngine().extract(text, skill, _RetryingProvider())

    # Every RawExtraction — real value rows AND declared-but-missing
    # placeholder rows — must carry the validator's attempts count (3).
    assert [r.field_name for r in results] == ["name", "age", "city"]
    assert [r.attempts for r in results] == [3, 3, 3], (
        "validator retry count must propagate to every declared field, "
        f"got {[r.attempts for r in results]}"
    )


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
        description="mappingproxy skill.",
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


def test_declared_field_names_pins_yaml_declaration_order_through_loader(
    tmp_path: Any,
) -> None:
    """Pin the ORDER of `declared_field_names` through the full YAML->Skill path.

    Issue #390: `declared_field_names` iterates `skill.output_schema["properties"]`
    and Python's dict is insertion-ordered in 3.7+, but nothing in the test
    suite asserts that the order round-trips unchanged from the YAML source
    through `DuplicateKeyDetectingSafeLoader` -> Pydantic's
    `dict[str, Any]` storage -> `deep_freeze_mapping` -> `MappingProxyType`
    -> `declared_field_names`'s `tuple(...)`. A future loader refactor
    (e.g. introducing `frozenset`/`set` for dedup, or switching the YAML
    loader to one that does not preserve mapping-node order) would silently
    reorder the response fields emitted by the API's response builder —
    and CLAUDE.md's "every declared field always present" invariant has
    ORDER as its natural companion for response determinism.

    This test locks the contract end-to-end with an intentionally
    non-alphabetical, non-insertion-tempting field order — so both
    "accidentally alphabetize" and "accidentally insertion-sort by
    hashing" regressions fail loud at parse time.
    """
    from pathlib import Path

    from app.features.extraction.skills.skill import Skill
    from app.features.extraction.skills.skill_yaml_schema import SkillYamlSchema

    # Deliberately NOT alphabetical, NOT reverse-alphabetical, NOT
    # length-sorted. This is the load-bearing expected order.
    expected_order: tuple[str, ...] = (
        "invoice_number",
        "amount_due",
        "issued_at",
        "vendor_name",
        "currency",
    )
    # Write YAML as literal text (not via `yaml.safe_dump`, which sorts keys
    # by default) so the source ORDER on disk is the exact ORDER the test
    # pins. This verifies that the YAML loader preserves node order all
    # the way through the stack — which is the bit that would silently
    # regress if someone swapped `DuplicateKeyDetectingSafeLoader` for a
    # loader that drops ordering, or if a future refactor routed
    # `properties` through a `set`/`frozenset` for dedup.
    yaml_text = (
        "name: pin-order\n"
        "version: 1\n"
        "description: Canonical fixture used to pin declared_field_names order.\n"
        "prompt: Extract the invoice fields.\n"
        "examples:\n"
        '  - input: "INV-1 total 10 USD issued 2024-01-01 from Acme"\n'
        "    output:\n"
        '      invoice_number: "INV-1"\n'
        '      amount_due: "10"\n'
        '      issued_at: "2024-01-01"\n'
        '      vendor_name: "Acme"\n'
        '      currency: "USD"\n'
        "output_schema:\n"
        "  type: object\n"
        "  properties:\n"
        "    invoice_number:\n"
        "      type: string\n"
        "    amount_due:\n"
        "      type: string\n"
        "    issued_at:\n"
        "      type: string\n"
        "    vendor_name:\n"
        "      type: string\n"
        "    currency:\n"
        "      type: string\n"
        "  required:\n"
        "    - invoice_number\n"
        "    - amount_due\n"
        "    - issued_at\n"
        "    - vendor_name\n"
        "    - currency\n"
    )
    path: Path = tmp_path / "1.yaml"
    path.write_text(yaml_text, encoding="utf-8")

    schema = SkillYamlSchema.load_from_file(path)
    skill = Skill.from_schema(schema)

    # The invariant: a list comparison so the failure diff shows the
    # exact position of any reorder regression.
    assert declared_field_names(skill) == expected_order
    assert list(declared_field_names(skill)) == list(expected_order)


def test_declared_field_names_is_tuple_not_set_so_order_is_observable(
    tmp_path: Any,
) -> None:
    """Companion guard for #390: the return type must be ``tuple``, not
    ``set``/``frozenset`` — a set return would structurally discard order
    and silently hide a future reorder regression from the pinning test.
    Return type is the contract, not an implementation detail.
    """
    from pathlib import Path

    from app.features.extraction.skills.skill import Skill
    from app.features.extraction.skills.skill_yaml_schema import SkillYamlSchema

    yaml_text = (
        "name: pin-return-type\n"
        "version: 1\n"
        "description: Return-type pinning fixture.\n"
        "prompt: Extract fields.\n"
        "examples:\n"
        '  - input: "x"\n'
        "    output:\n"
        '      a: "1"\n'
        "output_schema:\n"
        "  type: object\n"
        "  properties:\n"
        "    a:\n"
        "      type: string\n"
        "    b:\n"
        "      type: string\n"
        "  required:\n"
        "    - a\n"
    )
    path: Path = tmp_path / "1.yaml"
    path.write_text(yaml_text, encoding="utf-8")

    schema = SkillYamlSchema.load_from_file(path)
    skill = Skill.from_schema(schema)

    result = declared_field_names(skill)
    assert isinstance(result, tuple)
    assert result == ("a", "b")


async def test_extract_normalizes_malformed_langextract_interval_to_ungrounded(
    patch_extract: Any,
) -> None:
    """If LangExtract returns a `char_interval` with negative, equal, or
    inverted endpoints, the engine must coerce the resulting
    `RawExtraction` to ungrounded (`grounded=False`, offsets `None`) —
    otherwise the stricter `RawExtraction.__post_init__` invariants would
    explode and a single bad span would nuke the whole extraction.
    """

    text = "Alice is 30."
    skill = _build_skill(("a", "b", "c"))
    annotated = AnnotatedDocument(
        extractions=[
            _extraction("a", "val", -1, 5),  # negative start
            _extraction("b", "val", 5, 5),  # equal endpoints
            _extraction("c", "val", 10, 4),  # start > end
        ],
        text=text,
    )
    patch_extract(annotated)

    results = await ExtractionEngine().extract(text, skill, _FakeProvider())

    assert [r.field_name for r in results] == ["a", "b", "c"]
    for raw in results:
        assert raw.value == "val"
        assert raw.char_offset_start is None
        assert raw.char_offset_end is None
        assert raw.grounded is False


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


async def test_extract_with_empty_text_returns_placeholders_without_invoking_langextract(
    patch_extract: Any,
) -> None:
    """Empty input text must still honor the "every declared field always
    present" invariant (CLAUDE.md:104). The engine short-circuits LangExtract
    — no prompt is worth sending for empty text — but returns one placeholder
    `RawExtraction` per declared field so downstream assembly can still look
    each field up by name and see `status=missing`.
    """

    skill = _build_skill(("name", "age", "email"))

    results = await ExtractionEngine().extract("", skill, _FakeProvider())

    assert [r.field_name for r in results] == ["name", "age", "email"]
    assert all(r.value is None for r in results)
    assert all(r.grounded is False for r in results)
    assert all(r.char_offset_start is None for r in results)
    assert all(r.char_offset_end is None for r in results)
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
    adapter = _ValidatingLangExtractAdapter(provider, main_loop, timeout_seconds=30.0)

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

    adapter = _ValidatingLangExtractAdapter(
        _LoopCapturingProvider(),
        main_loop,
        timeout_seconds=30.0,
    )

    def _run_infer() -> None:
        list(adapter.infer(["p1", "p2", "p3"]))

    await _asyncio.to_thread(_run_infer)

    assert len(captured_loops) == 3
    assert all(lid == expected_loop_id for lid in captured_loops), (
        f"expected all prompts on main loop {expected_loop_id}, got {captured_loops}"
    )


async def test_validating_adapter_infer_raises_intelligence_timeout_when_generate_hangs() -> None:
    """If `provider.generate` hangs past `timeout_seconds`, the adapter must
    raise `IntelligenceTimeoutError` (not block forever on `future.result()`
    and not leak `concurrent.futures.TimeoutError`). Fix for issue #152:
    unbounded `future.result()` was a thread-pool leak under sustained Ollama
    hangs. We assert a hang is bounded by the configured timeout.
    """
    import asyncio as _asyncio

    from app.exceptions import IntelligenceTimeoutError

    hang_started = _asyncio.Event()

    class _HangingProvider:
        async def generate(
            self,
            prompt: str,
            output_schema: dict[str, Any],
        ) -> GenerationResult:
            _ = prompt
            _ = output_schema
            hang_started.set()
            # Simulate an unresponsive Ollama with a delay that exceeds the
            # adapter timeout (0.2s) without stranding a non-daemon worker
            # thread for an hour if timeout handling ever regresses. A few
            # seconds is ample headroom vs the 0.2s budget below.
            await _asyncio.sleep(3)
            msg = "unreachable — adapter should have timed out"  # pragma: no cover
            raise AssertionError(msg)  # pragma: no cover

    main_loop = _asyncio.get_running_loop()
    adapter = _ValidatingLangExtractAdapter(
        _HangingProvider(),
        main_loop,
        timeout_seconds=0.2,
    )

    def _run_infer() -> None:
        list(adapter.infer(["prompt-that-hangs"]))

    # Start the blocking adapter call in the background, wait until the
    # provider coroutine has definitely started, and only then assert that
    # the adapter returns promptly with IntelligenceTimeoutError. Avoids a
    # race where the adapter's 0.2s cancel could fire before the main loop
    # has scheduled `generate` on slow/busy CI runners — in which case the
    # old assertion (`hang_started.wait()` after the thread returned) would
    # flake because `hang_started` never got set.
    infer_task = _asyncio.create_task(_asyncio.to_thread(_run_infer))
    await _asyncio.wait_for(hang_started.wait(), timeout=1.0)

    # The overall await must return promptly with IntelligenceTimeoutError,
    # not hang the caller. `asyncio.wait_for` as a belt-and-braces guard
    # proves we were not rescued by the test harness killing the loop.
    with pytest.raises(IntelligenceTimeoutError) as exc_info:
        await _asyncio.wait_for(infer_task, timeout=5.0)
    assert exc_info.value.code == "INTELLIGENCE_TIMEOUT"


async def test_validating_adapter_infer_propagates_inner_timeout_error_distinct_from_adapter_timeout() -> (
    None
):
    """If `provider.generate` itself raises `TimeoutError` (e.g. an inner
    `asyncio.wait_for` inside a provider implementation expires), the adapter
    must propagate that exception — NOT remap it to `IntelligenceTimeoutError`.

    Regression for Copilot review on PR #173: in CPython 3.11+,
    `concurrent.futures.TimeoutError is TimeoutError is asyncio.TimeoutError`.
    A bare `except concurrent.futures.TimeoutError` would also catch inner
    `TimeoutError` raised by the underlying coroutine, incorrectly converting
    inner failures into `IntelligenceTimeoutError` and discarding the original
    cause. The adapter distinguishes the two cases by checking
    `future.done()` — only a pending future means `future.result(timeout=...)`
    itself timed out; a done future with a `TimeoutError` result came from
    the coroutine body.
    """
    import asyncio as _asyncio

    from app.exceptions import IntelligenceTimeoutError

    class _InnerTimeoutProvider:
        async def generate(
            self,
            prompt: str,
            output_schema: dict[str, Any],
        ) -> GenerationResult:
            _ = prompt
            _ = output_schema
            msg = "provider's own timeout — NOT adapter's future.result timeout"
            raise TimeoutError(msg)

    main_loop = _asyncio.get_running_loop()
    adapter = _ValidatingLangExtractAdapter(
        _InnerTimeoutProvider(),
        main_loop,
        timeout_seconds=30.0,
    )

    def _run_infer() -> None:
        list(adapter.infer(["prompt"]))

    # The inner TimeoutError must propagate — it is NOT an adapter timeout.
    # We assert `not isinstance(..., IntelligenceTimeoutError)` defensively
    # so a regression that re-maps inner timeouts is caught even if a
    # future refactor makes IntelligenceTimeoutError extend TimeoutError.
    with pytest.raises(TimeoutError) as exc_info:
        await _asyncio.to_thread(_run_infer)
    assert not isinstance(exc_info.value, IntelligenceTimeoutError)
    assert "provider's own timeout" in str(exc_info.value)


async def test_validating_adapter_infer_returns_settled_result_on_boundary_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for Copilot review on PR #173: there is a boundary race
    where `future.result(timeout=...)` raises `TimeoutError` AND the future
    settles (successfully) just before we inspect `future.done()`. In that
    case the adapter must return the settled result — NOT re-raise the
    stale `TimeoutError` from the timed-out `.result(timeout=...)` call.

    Before the fix, `if future.done(): raise` would leak the `TimeoutError`
    past the adapter for a future that had actually completed successfully.
    After the fix, a done future gets `.result()` called WITHOUT a timeout,
    returning the settled value (or re-raising the coroutine's real error).
    """
    import asyncio as _asyncio
    import concurrent.futures as _cf

    from app.features.extraction.intelligence.generation_result import GenerationResult

    settled_payload = GenerationResult(
        data={"ok": True, "source": "settled-just-in-time"},
        raw_output="{}",
        attempts=1,
    )

    class _RacyFuture:
        """Simulates the boundary race: first `.result(timeout=...)` raises
        TimeoutError, but `.done()` returns True and `.result()` (no timeout)
        returns the already-settled payload."""

        def __init__(self, settled: GenerationResult) -> None:
            self._settled = settled
            self._timed_out_once = False

        def result(self, timeout: float | None = None) -> GenerationResult:
            if timeout is not None and not self._timed_out_once:
                self._timed_out_once = True
                raise _cf.TimeoutError
            return self._settled

        def done(self) -> bool:
            return True

        def cancel(self) -> bool:
            return False

    def _stub_run_coro_threadsafe(coro: Any, _loop: Any) -> _RacyFuture:
        # Close the coroutine so we don't leak a "never awaited" warning.
        coro.close()
        return _RacyFuture(settled_payload)

    monkeypatch.setattr(
        "app.features.extraction.extraction.extraction_engine.asyncio.run_coroutine_threadsafe",
        _stub_run_coro_threadsafe,
    )

    main_loop = _asyncio.get_running_loop()
    adapter = _ValidatingLangExtractAdapter(
        _FakeProvider(),
        main_loop,
        timeout_seconds=0.001,  # small; the stub controls behavior regardless
    )

    def _run_infer() -> list[list[Any]]:
        return [list(batch) for batch in adapter.infer(["prompt"])]

    results = await _asyncio.to_thread(_run_infer)

    assert len(results) == 1
    assert len(results[0]) == 1
    assert json.loads(results[0][0].output) == {
        "ok": True,
        "source": "settled-just-in-time",
    }


async def test_validating_adapter_infer_returns_normally_when_generate_completes_in_time() -> None:
    """Positive regression for the timeout fix: when `provider.generate` finishes
    well within `timeout_seconds`, `infer` must yield the expected ScoredOutput
    without surfacing any timeout error. Guards against the timeout guard
    accidentally short-circuiting the fast path.
    """
    import asyncio as _asyncio

    provider = _FakeProvider()
    main_loop = _asyncio.get_running_loop()
    adapter = _ValidatingLangExtractAdapter(
        provider,
        main_loop,
        timeout_seconds=30.0,
    )

    def _run_infer() -> list[list[Any]]:
        return [list(batch) for batch in adapter.infer(["quick-prompt"])]

    results = await _asyncio.to_thread(_run_infer)

    assert len(results) == 1
    assert len(results[0]) == 1
    assert json.loads(results[0][0].output) == {"ok": True}


async def test_extract_passes_ollama_timeout_from_settings_into_adapter(
    patch_extract: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ExtractionEngine.extract` must build the adapter with the
    `ollama_timeout_seconds` value from `Settings`, so the timeout honoured
    inside `future.result(timeout=...)` is the configured Ollama budget.
    """
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "7.5")

    text = "Alice is 30."
    skill = _build_skill(("name",))
    patch_extract(
        AnnotatedDocument(
            extractions=[_extraction("name", "Alice", 0, 5)],
            text=text,
        ),
    )

    await ExtractionEngine().extract(text, skill, _FakeProvider())

    call_kwargs = patch_extract.calls[0]  # type: ignore[attr-defined]
    model = call_kwargs["model"]
    assert isinstance(model, _ValidatingLangExtractAdapter)
    # The adapter records the effective budget so downstream
    # `future.result(timeout=...)` and IntelligenceTimeoutError payload
    # both use the configured value.
    assert model._timeout_seconds == 7.5  # noqa: SLF001 — asserting on private state is the point


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
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

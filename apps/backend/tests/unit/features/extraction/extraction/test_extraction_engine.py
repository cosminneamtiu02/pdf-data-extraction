"""Unit tests for ExtractionEngine.

The engine is a thin wrapper around `langextract.extract`. Unit tests patch
the module-level `langextract.extract` attribute on the engine module and
verify the transformation from LangExtract's native `AnnotatedDocument` /
`Extraction` shape into feature-owned `RawExtraction` instances. End-to-end
integration with real LangExtract is covered in
`tests/integration/features/extraction/test_extraction_engine_integration.py`.
"""

from __future__ import annotations

import inspect
from types import MappingProxyType
from typing import Any

import pytest
from langextract.core.data import AnnotatedDocument, CharInterval, Extraction

from app.features.extraction.extraction import extraction_engine as engine_module
from app.features.extraction.extraction.extraction_engine import ExtractionEngine
from app.features.extraction.intelligence.generation_result import GenerationResult
from app.features.extraction.skills.skill import Skill
from app.features.extraction.skills.skill_example import SkillExample


class _FakeProvider:
    """Minimal IntelligenceProvider double. LangExtract bypasses `generate`
    via the `infer` path (see dual-interface note in the engine docstring),
    so unit tests that mock `langextract.extract` never exercise either.
    """

    async def generate(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> GenerationResult:
        _ = prompt
        _ = output_schema
        return GenerationResult(data={}, attempts=1, raw_output="{}")


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


async def test_extract_with_empty_text_returns_empty_list_without_invoking_langextract(
    patch_extract: Any,
) -> None:
    skill = _build_skill(("name",))

    results = await ExtractionEngine().extract("", skill, _FakeProvider())

    assert results == []
    assert patch_extract.calls == []  # type: ignore[attr-defined]


async def test_extract_with_no_extractions_returns_empty_list(patch_extract: Any) -> None:
    skill = _build_skill(("name",))
    patch_extract(AnnotatedDocument(extractions=[], text="hello"))

    results = await ExtractionEngine().extract("hello", skill, _FakeProvider())

    assert results == []


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

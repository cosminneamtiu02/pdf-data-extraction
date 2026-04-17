"""Unit tests for `SkillYamlSchema` — structural + deep validation."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills import SkillYamlSchema

from .conftest import REMOVE, SkillYamlFactory


def _reason(err: SkillValidationFailedError) -> str:
    """Extract the `reason` string from an aggregated skill validation error.

    `DomainError.params` is typed as `BaseModel | None` so we go through
    `model_dump()` rather than attribute access to stay pyright-strict-clean.
    """
    assert err.params is not None
    dumped = err.params.model_dump()
    value = dumped["reason"]
    assert isinstance(value, str)
    return value


def test_load_from_file_returns_populated_instance(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml()

    schema = SkillYamlSchema.load_from_file(path)

    assert schema.name == "invoice"
    assert schema.version == 1
    assert schema.prompt == "Extract invoice header fields."
    assert len(schema.examples) == 1
    assert schema.examples[0].output == {"number": "INV-1"}
    assert schema.output_schema["required"] == ["number"]
    assert schema.description is None
    assert schema.docling is None


def test_missing_prompt_raises_pydantic_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(prompt=REMOVE)

    with pytest.raises(ValidationError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    errors = exc_info.value.errors()
    assert any(err["loc"] == ("prompt",) and err["type"].startswith("missing") for err in errors)


def test_invalid_json_schema_raises_skill_validation_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(output_schema={"type": "notathing"})

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert "output_schema is not a valid JSONSchema" in _reason(exc_info.value)


def test_example_missing_required_field_reports_index_and_field(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(
        output_schema={
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string"},
            },
            "required": ["a", "b"],
        },
        examples=[{"input": "x", "output": {"a": "1"}}],
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "example index 0" in reason
    assert "'b' is a required property" in reason


def test_filename_body_version_mismatch_raises(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(filename="2.yaml", version=1)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert "filename version 2 does not match body version 1" in _reason(exc_info.value)


def test_two_examples_second_violates_schema_reports_index_and_path(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(
        output_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
        examples=[
            {"input": "one", "output": {"count": 1}},
            {"input": "two", "output": {"count": "two"}},
        ],
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "example index 1" in reason
    assert "/count" in reason


def test_multiple_violations_in_single_example_all_aggregated(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """Prove aggregation WITHIN one example — both missing fields surface."""
    path = write_skill_yaml(
        output_schema={
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
        examples=[{"input": "x", "output": {}}],
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "'a' is a required property" in reason
    assert "'b' is a required property" in reason


def test_empty_examples_list_raises_pydantic_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(examples=[])

    with pytest.raises(ValidationError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert any(err["loc"] == ("examples",) for err in exc_info.value.errors())


def test_version_zero_raises_pydantic_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(filename="0.yaml", version=0)

    with pytest.raises(ValidationError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert any(err["loc"] == ("version",) for err in exc_info.value.errors())


def test_empty_name_raises_pydantic_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(name="")

    with pytest.raises(ValidationError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert any(err["loc"] == ("name",) for err in exc_info.value.errors())


def test_uppercase_name_raises_pydantic_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(name="Has-UPPERCASE")

    with pytest.raises(ValidationError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert any(err["loc"] == ("name",) for err in exc_info.value.errors())


def test_empty_prompt_raises_pydantic_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(prompt="")

    with pytest.raises(ValidationError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert any(err["loc"] == ("prompt",) for err in exc_info.value.errors())


def test_non_integer_filename_stem_raises(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(filename="v1.yaml")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert "'v1' is not an integer" in _reason(exc_info.value)


def test_multiple_example_violations_all_reported(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml(
        output_schema={
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
        examples=[
            {"input": "x", "output": {}},
            {"input": "y", "output": {"a": 1, "b": "wrong"}},
        ],
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "example index 0" in reason
    assert "example index 1" in reason


def test_optional_description_defaults_to_none(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml()

    schema = SkillYamlSchema.load_from_file(path)

    assert schema.description is None


def test_optional_docling_defaults_to_none(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    path = write_skill_yaml()

    schema = SkillYamlSchema.load_from_file(path)

    assert schema.docling is None


def test_unparseable_yaml_raises_skill_validation_error(tmp_path: Path) -> None:
    """Broken YAML must surface as SkillValidationFailedError, not raw yaml.YAMLError."""
    path = tmp_path / "1.yaml"
    path.write_text("key: [unterminated\n", encoding="utf-8")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert "is not parseable" in _reason(exc_info.value)


def test_yaml_body_that_is_not_a_mapping_raises(tmp_path: Path) -> None:
    path = tmp_path / "1.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert "did not parse to a mapping" in _reason(exc_info.value)


def test_description_and_docling_populate_when_provided(tmp_path: Path) -> None:
    body: dict[str, object] = {
        "name": "invoice",
        "version": 1,
        "description": "Invoice header extractor.",
        "prompt": "p",
        "examples": [{"input": "x", "output": {"a": "1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        },
        "docling": {"ocr": "auto", "table_mode": "fast"},
    }
    path = tmp_path / "1.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")

    schema = SkillYamlSchema.load_from_file(path)

    assert schema.description == "Invoice header extractor."
    assert schema.docling is not None
    assert schema.docling.ocr == "auto"
    assert schema.docling.table_mode == "fast"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("ocr", "banana"),
        ("ocr", "on"),
        ("table_mode", "turbo"),
        ("table_mode", "slow"),
    ],
)
def test_docling_rejects_invalid_values_at_load_time(
    tmp_path: Path, field: str, bad_value: str
) -> None:
    """Typos in a skill's `docling:` block must fail at YAML load, not at runtime.

    Prior to PDFX-E002-F001 hardening, `SkillDoclingConfig.ocr` and
    `.table_mode` were typed as plain `str`, so `ocr: banana` loaded
    successfully and only surfaced as a runtime `DoclingConfig` ValueError
    deep in the parser layer — exactly the silent-drift the closed-shape
    validator is supposed to prevent.
    """
    body: dict[str, object] = {
        "name": "invoice",
        "version": 1,
        "prompt": "p",
        "examples": [{"input": "x", "output": {"a": "1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        },
        "docling": {field: bad_value},
    }
    path = tmp_path / "1.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")

    with pytest.raises(ValidationError):
        SkillYamlSchema.load_from_file(path)


@pytest.mark.parametrize(
    "empty_schema",
    [
        {},
        {"type": "object"},
        {"type": "object", "properties": {}},
    ],
)
def test_output_schema_with_zero_declared_properties_rejected_at_load_time(
    write_skill_yaml: SkillYamlFactory, empty_schema: dict[str, object]
) -> None:
    """Object schemas must declare at least one property at load time.

    A skill whose `output_schema` is a type-`object` schema with no `properties`
    (or an empty `properties` mapping) is structurally unable to produce any
    field — the extraction engine would later return `STRUCTURED_OUTPUT_FAILED`
    at request time, turning an authoring mistake into a confusing deferred
    runtime failure. GitHub issue #114 tracks this; fail fast at load time so
    the author sees the problem in the same error-aggregation pass as other
    skill-YAML mistakes.
    """
    path = write_skill_yaml(
        output_schema=empty_schema,
        examples=[{"input": "x", "output": {}}],
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "output_schema" in reason
    assert "properties" in reason


def test_output_schema_with_one_property_still_loads_successfully(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """Regression guard: a minimal non-empty object schema must still load.

    The non-empty-properties rule MUST NOT regress the existing happy path
    where a skill declares at least one output field.
    """
    path = write_skill_yaml(
        output_schema={
            "type": "object",
            "properties": {"only_field": {"type": "string"}},
        },
        examples=[{"input": "x", "output": {"only_field": "v"}}],
    )

    schema = SkillYamlSchema.load_from_file(path)

    assert schema.output_schema["properties"] == {"only_field": {"type": "string"}}

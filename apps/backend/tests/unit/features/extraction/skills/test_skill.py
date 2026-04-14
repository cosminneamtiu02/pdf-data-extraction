"""Unit tests for the `Skill` runtime domain dataclass."""

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.features.extraction.skills import (
    Skill,
    SkillDoclingConfig,
    SkillExample,
    SkillYamlSchema,
)


def _valid_schema(**overrides: object) -> SkillYamlSchema:
    base: dict[str, object] = {
        "name": "invoice",
        "version": 1,
        "prompt": "Extract.",
        "examples": [SkillExample(input="x", output={"a": "1"})],
        "output_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        },
    }
    base.update(overrides)
    return SkillYamlSchema.model_validate(base)


def test_skill_from_schema_populates_all_fields() -> None:
    schema = _valid_schema()

    skill = Skill.from_schema(schema)

    assert skill.name == "invoice"
    assert skill.version == 1
    assert skill.prompt == "Extract."
    assert len(skill.examples) == 1
    assert skill.output_schema["required"] == ("a",)
    assert isinstance(skill.docling_config, SkillDoclingConfig)


def test_skill_is_frozen() -> None:
    schema = _valid_schema()
    skill = Skill.from_schema(schema)

    with pytest.raises(dataclasses.FrozenInstanceError):
        skill.name = "other"  # type: ignore[misc]


def test_skill_output_schema_is_read_only() -> None:
    """Frozen dataclass protects attribute reassignment; MappingProxyType
    protects against in-place mutation of the schema dict itself.
    """
    schema = _valid_schema()
    skill = Skill.from_schema(schema)

    with pytest.raises(TypeError):
        skill.output_schema["injected"] = True  # type: ignore[index]


def test_skill_output_schema_is_deeply_immutable() -> None:
    """Nested dicts and lists inside `output_schema` must also be frozen —
    a shallow `MappingProxyType` would leave `skill.output_schema["properties"]`
    mutable, which violates the "immutable runtime object" contract.
    """
    schema = _valid_schema()
    skill = Skill.from_schema(schema)

    # Nested dict: `properties` → `{"a": {"type": "string"}}`
    with pytest.raises(TypeError):
        skill.output_schema["properties"]["a"]["type"] = "integer"  # type: ignore[index]

    with pytest.raises(TypeError):
        skill.output_schema["properties"]["injected"] = {}  # type: ignore[index]

    # Nested list → frozen as tuple, so `.append` and index assignment fail
    required = skill.output_schema["required"]
    assert isinstance(required, tuple)
    with pytest.raises((TypeError, AttributeError)):
        required.append("b")  # type: ignore[attr-defined]


def test_docling_config_is_merged_not_raw_override() -> None:
    schema = _valid_schema(docling=SkillDoclingConfig(ocr="auto"))
    default = SkillDoclingConfig(ocr="none", table_mode="fast")

    skill = Skill.from_schema(schema, default_docling=default)

    # Override wins for ocr, default kept for table_mode.
    assert skill.docling_config.ocr == "auto"
    assert skill.docling_config.table_mode == "fast"


def test_docling_config_defaults_when_no_override_or_default() -> None:
    schema = _valid_schema()

    skill = Skill.from_schema(schema)

    assert skill.docling_config == SkillDoclingConfig()


def test_from_schema_after_load_from_file_merges_yaml_docling(
    tmp_path: Path,
) -> None:
    """End-to-end: YAML on disk -> load_from_file -> from_schema -> merged config."""
    body: dict[str, object] = {
        "name": "invoice",
        "version": 1,
        "prompt": "Extract.",
        "examples": [{"input": "x", "output": {"a": "1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        },
        "docling": {"ocr": "auto"},
    }
    path = tmp_path / "1.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")

    schema = SkillYamlSchema.load_from_file(path)
    skill = Skill.from_schema(
        schema,
        default_docling=SkillDoclingConfig(ocr="none", table_mode="fast"),
    )

    assert isinstance(skill.docling_config, SkillDoclingConfig)
    assert skill.docling_config.ocr == "auto"
    assert skill.docling_config.table_mode == "fast"


def test_skill_layer_does_not_transitively_import_docling() -> None:
    """Guard the import-linter containment rule from PDFX-E007-F004."""
    code = (
        "import sys\n"
        "from app.features.extraction.skills import "
        "Skill, SkillDoclingConfig, SkillExample, SkillYamlSchema\n"
        "assert 'docling' not in sys.modules, "
        "f'docling leaked: {sorted(k for k in sys.modules if \"docling\" in k)}'\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

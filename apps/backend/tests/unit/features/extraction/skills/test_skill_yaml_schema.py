"""Unit tests for `SkillYamlSchema` — structural + deep validation."""

from pathlib import Path

import pytest
import yaml

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
    assert schema.description == "Invoice header extractor."
    assert schema.docling is None


def test_missing_prompt_raises_skill_validation_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """Missing-required-field Pydantic errors must be wrapped (issue #214)."""
    path = write_skill_yaml(prompt=REMOVE)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "prompt" in reason
    assert "\n" not in reason


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


def test_empty_examples_list_raises_skill_validation_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """Empty examples list must be wrapped (issue #214)."""
    path = write_skill_yaml(examples=[])

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "examples" in reason


def test_version_zero_raises_skill_validation_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """A zero version must be wrapped (issue #214)."""
    path = write_skill_yaml(filename="0.yaml", version=0)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "version" in reason


def test_empty_name_raises_skill_validation_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """An empty name must be wrapped (issue #214)."""
    path = write_skill_yaml(name="")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "name" in reason


def test_uppercase_name_raises_skill_validation_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """A name with disallowed characters must be wrapped (issue #214)."""
    path = write_skill_yaml(name="Has-UPPERCASE")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "name" in reason


def test_empty_prompt_raises_skill_validation_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """An empty prompt must be wrapped (issue #214)."""
    path = write_skill_yaml(prompt="")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "prompt" in reason


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
    path = write_skill_yaml(description=REMOVE)

    schema = SkillYamlSchema.load_from_file(path)

    assert schema.description is None


def test_duplicate_top_level_keys_raise_skill_validation_error(tmp_path: Path) -> None:
    """Two ``prompt:`` keys at the top level surface as a curated error.

    PyYAML's default ``SafeLoader`` silently collapses repeated keys
    last-wins; the custom ``DuplicateKeyDetectingSafeLoader`` rejects them
    so an author's copy-paste typo cannot deploy a skill whose authored
    intent was silently erased. Issue #208.
    """
    path = tmp_path / "1.yaml"
    path.write_text(
        "name: invoice\n"
        "version: 1\n"
        "description: Invoice header extractor.\n"
        "prompt: first\n"
        "prompt: second\n"
        'examples:\n  - input: "x"\n    output: {number: "1"}\n'
        "output_schema: {type: object, properties: {number: {type: string}}}\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "duplicate key" in reason
    assert "'prompt'" in reason


def test_duplicate_output_schema_property_keys_raise_skill_validation_error(
    tmp_path: Path,
) -> None:
    """Two entries with the same name under ``output_schema.properties`` fail.

    Without the duplicate-key detection, the second entry would silently
    overwrite the first and the schema would validate as if the first had
    never been authored. The defect would only surface at request time on
    live traffic. Issue #208.
    """
    path = tmp_path / "1.yaml"
    path.write_text(
        "name: invoice\n"
        "version: 1\n"
        "description: Invoice header extractor.\n"
        'prompt: "Extract."\n'
        'examples:\n  - input: "x"\n    output: {amount_due: "1"}\n'
        "output_schema:\n"
        "  type: object\n"
        "  properties:\n"
        "    amount_due:\n"
        "      type: string\n"
        "    amount_due:\n"
        "      type: integer\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "duplicate key" in reason
    assert "'amount_due'" in reason


def test_unhashable_mapping_key_raises_skill_validation_error(
    tmp_path: Path,
) -> None:
    """A sequence used as a mapping key must surface as SkillValidationFailedError.

    PyYAML's upstream ``SafeConstructor.construct_mapping`` explicitly
    checks ``isinstance(key, Hashable)`` and raises
    ``yaml.constructor.ConstructorError`` on failure. Without that guard
    our ``if key in mapping`` would raise a bare ``TypeError`` that
    bypasses the ``except yaml.YAMLError`` wrapping in
    ``SkillYamlSchema.load_from_file``, surfacing as a raw traceback
    instead of the curated ``SkillValidationFailedError`` envelope.
    """
    path = tmp_path / "1.yaml"
    # ``? [a, b]`` is YAML's explicit-key syntax for a sequence-valued key.
    # The constructed Python object is a list, which is unhashable.
    path.write_text(
        "name: invoice\n"
        "version: 1\n"
        "description: Invoice header extractor.\n"
        'prompt: "Extract."\n'
        'examples:\n  - input: "x"\n    output: {number: "1"}\n'
        "output_schema:\n"
        "  type: object\n"
        "  properties:\n"
        "    ? [a, b]\n"
        "    : {type: string}\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "unhashable" in reason.lower() or "not hashable" in reason.lower()


def test_merge_key_still_applied_by_duplicate_key_detecting_loader(
    tmp_path: Path,
) -> None:
    """YAML merge keys (``<<: *anchor``) must still flatten into the mapping.

    Parity guard for the custom loader: our constructor replaces only the
    duplicate-detection step of PyYAML's default ``SafeConstructor.construct_mapping``,
    not the ``flatten_mapping`` call that applies YAML merge keys. Without
    ``loader.flatten_mapping(node)`` the merge-key syntax would silently
    disappear from the loaded dict — a behavior change vs. plain
    ``yaml.safe_load`` that would be a footgun for skill authors.
    """
    path = tmp_path / "1.yaml"
    path.write_text(
        "name: invoice\n"
        "version: 1\n"
        'prompt: "Extract."\n'
        'examples:\n  - input: "x"\n    output: {a: "1"}\n'
        "output_schema:\n"
        "  type: object\n"
        "  properties:\n"
        "    defaults: &defaults\n"
        "      type: string\n"
        "    a:\n"
        "      <<: *defaults\n",
        encoding="utf-8",
    )

    schema = SkillYamlSchema.load_from_file(path)

    assert schema.output_schema["properties"]["a"]["type"] == "string"


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

    # Issue #214: Pydantic `ValidationError` must be wrapped as
    # `SkillValidationFailedError` with a curated single-line reason so
    # `SkillLoader`'s aggregate output stays uniform across failure paths.
    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert field in reason


@pytest.mark.parametrize(
    "empty_schema",
    [
        {},
        {"type": "object"},
        {"type": "object", "properties": {}},
        # Draft 7 allows `type` to be a list of types; when that list contains
        # "object", the schema still permits object-shaped output and so must
        # be subject to the same "at least one property" invariant. Without
        # the list-handling branch this case would silently slip through.
        {"type": ["object", "null"]},
        {"type": ["object", "null"], "properties": {}},
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


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        # Object-permitting — must be flagged when zero properties:
        ({}, True),
        ({"type": "object"}, True),
        ({"type": "object", "properties": {}}, True),
        ({"type": ["object", "null"]}, True),
        ({"type": ("object", "null")}, True),  # tuple form, for Python callers
        ({"type": ["null", "object"], "properties": {}}, True),
        # Object-permitting but has at least one property — not empty:
        ({"type": "object", "properties": {"a": {"type": "string"}}}, False),
        (
            {"type": ["object", "null"], "properties": {"a": {"type": "string"}}},
            False,
        ),
        # Not object-permitting — outside scope:
        ({"type": "string"}, False),
        ({"type": ["string", "null"]}, False),
        ({"type": ["integer", "number"]}, False),
    ],
)
def test_is_empty_object_schema_classifies_draft7_type_variants(
    schema: dict[str, object],
    expected: bool,  # noqa: FBT001 -- parametrized expected return value, not a flag argument
) -> None:
    """`_is_empty_object_schema` must handle string- and list-form `type`.

    Draft 7 allows `type` to be either a single string OR a list of strings
    (see jsonschema 4.26.0 meta-schema). When the list contains `"object"`,
    the schema still permits object-shaped extraction output and so falls
    under the same "at least one declared property" invariant as the plain
    `type: object` form. Before this case was handled, `{"type": ["object",
    "null"]}` with zero properties silently slipped past the validator.
    """
    from app.features.extraction.skills.skill_yaml_schema import (
        _is_empty_object_schema,
    )

    assert _is_empty_object_schema(schema) is expected


def test_is_empty_object_schema_catches_additional_properties_only_schema() -> None:
    """`additionalProperties`-only schemas are empty from the engine's perspective.

    `{"type": "object", "additionalProperties": {"type": "string"}}` validates
    structurally at Draft 7 meta-validation and also accepts arbitrary keys at
    request time. BUT the extraction engine derives field names strictly from
    top-level `properties` (see `declared_field_names`), so this schema yields
    zero declared fields and every extraction returns empty — which the
    engine then surfaces as a confusing deferred 502 `all-failed` response
    (issue #388, same silent-failure shape as issue #114). Fail fast at load
    time in the same branch as the missing-`properties` case.
    """
    from app.features.extraction.skills.skill_yaml_schema import (
        _is_empty_object_schema,
    )

    assert (
        _is_empty_object_schema({"type": "object", "additionalProperties": {"type": "string"}})
        is True
    )
    # Also trips when properties is present but empty AND additionalProperties
    # is permissive — same zero-declared-fields outcome.
    assert (
        _is_empty_object_schema(
            {
                "type": "object",
                "properties": {},
                "additionalProperties": {"type": "number"},
            }
        )
        is True
    )
    # The `additionalProperties: True` shorthand (explicit "allow anything")
    # is also empty when no properties are declared.
    assert _is_empty_object_schema({"type": "object", "additionalProperties": True}) is True


def test_is_empty_object_schema_accepts_additional_properties_false() -> None:
    """`additionalProperties: False` + zero properties is still flagged as empty.

    When the author pins `additionalProperties: False` AND declares no
    properties, the schema literally accepts only `{}` — it cannot produce
    any extraction field under any circumstance. This was already flagged
    as empty before #388 (it is covered by the "no properties" branch), and
    it must stay flagged after #388. This test locks that invariant so the
    additionalProperties-aware branch does not accidentally flip the sign
    for the `False` case.
    """
    from app.features.extraction.skills.skill_yaml_schema import (
        _is_empty_object_schema,
    )

    assert _is_empty_object_schema({"type": "object", "additionalProperties": False}) is True
    assert (
        _is_empty_object_schema({"type": "object", "properties": {}, "additionalProperties": False})
        is True
    )


def test_skill_loader_rejects_additional_properties_only_output_schema_with_clear_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """Load-time rejection must name `additionalProperties` in its reason.

    The fix for issue #388 not only flags the schema but also updates the
    loader's human-readable reason so the skill author immediately sees that
    the root cause is the `additionalProperties`-only shape — not a cryptic
    deferred 502 at request time. The reason must mention
    `additionalProperties` by name so it is trivially greppable and
    actionable in the boot log.
    """
    path = write_skill_yaml(
        output_schema={
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        examples=[{"input": "x", "output": {}}],
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "output_schema" in reason
    assert "properties" in reason
    assert "additionalProperties" in reason


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        pytest.param(
            {"anyOf": [{"type": "string"}, {"type": "number"}]},
            ["anyOf"],
            id="anyOf",
        ),
        pytest.param(
            {"oneOf": [{"type": "string"}, {"type": "object"}]},
            ["oneOf"],
            id="oneOf",
        ),
        pytest.param(
            {"allOf": [{"type": "object", "properties": {"x": {"type": "string"}}}]},
            ["allOf"],
            id="allOf",
        ),
        pytest.param({"$ref": "#/definitions/Foo"}, ["$ref"], id="ref"),
    ],
)
def test_detect_unsupported_composition_root_keys_returns_triggering_keys(
    schema: dict[str, object],
    expected: list[str],
) -> None:
    """`_detect_unsupported_composition_root_keys` returns the exact keys
    present so the rejection message names the offending root shape (PR #315
    review follow-up). Issue #289.
    """
    from app.features.extraction.skills.skill_yaml_schema import (
        _detect_unsupported_composition_root_keys,
    )

    assert _detect_unsupported_composition_root_keys(schema) == expected


def test_detect_unsupported_composition_root_keys_returns_empty_for_plain_object() -> None:
    """Plain object schemas with explicit `properties` must not be mistaken
    for composition roots. The helper returns an empty list so callers can
    treat it as falsy (`if detected: ...`).
    """
    from app.features.extraction.skills.skill_yaml_schema import (
        _detect_unsupported_composition_root_keys,
    )

    assert (
        _detect_unsupported_composition_root_keys(
            {"type": "object", "properties": {"a": {"type": "string"}}}
        )
        == []
    )


def test_detect_unsupported_composition_root_keys_returns_empty_for_type_array() -> None:
    """Array-typed root schemas are NOT composition/$ref roots — they have
    explicit ``type: array`` semantics that `declared_field_names` ignores
    elsewhere. The helper should return an empty list for them; the actual
    array-root rejection (if any) is the concern of `_is_empty_object_schema`
    or future policy. (PR #315 review: explicit ``type: array`` coverage.)
    """
    from app.features.extraction.skills.skill_yaml_schema import (
        _detect_unsupported_composition_root_keys,
    )

    assert (
        _detect_unsupported_composition_root_keys({"type": "array", "items": {"type": "string"}})
        == []
    )


def test_detect_unsupported_composition_root_keys_fires_on_mixed_properties_plus_allof() -> None:
    """A schema that mixes top-level `properties` with a composition keyword
    (e.g. `allOf` for extra constraints) is still rejected under current
    policy. The extraction engine only reads top-level `properties`, but the
    author is signaling they want composition semantics that the engine
    can't honour — failing loud with a named-key error is better than
    pretending the composition metadata applies. If a future PR teaches
    the validator to allow the mixed case, this test needs to flip along
    with the policy. (PR #315 review: mixed-properties-plus-composition
    coverage.)
    """
    from app.features.extraction.skills.skill_yaml_schema import (
        _detect_unsupported_composition_root_keys,
    )

    mixed_schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "allOf": [{"required": ["a"]}],
    }
    assert _detect_unsupported_composition_root_keys(mixed_schema) == ["allOf"]


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({"anyOf": [{"type": "object"}, {"type": "object"}]}, id="anyOf"),
        pytest.param({"oneOf": [{"type": "object"}, {"type": "object"}]}, id="oneOf"),
        pytest.param(
            {"allOf": [{"type": "object", "properties": {"x": {"type": "string"}}}]},
            id="allOf",
        ),
        pytest.param({"$ref": "#/definitions/Foo"}, id="ref"),
    ],
)
def test_composition_root_schema_fails_with_clearer_error_not_empty_object(
    write_skill_yaml: SkillYamlFactory,
    schema: dict[str, object],
) -> None:
    """Composition/$ref-rooted output_schemas must fail load with a specific
    error that names the root shape and the `declared_field_names` constraint
    — NOT the generic "must declare at least one entry in 'properties'"
    message, which was misleading to skill authors using these valid Draft 7
    root shapes (PR #315 review follow-up).
    """
    path = write_skill_yaml(
        output_schema=schema,
        examples=[{"input": "x", "output": {}}],
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "composition" in reason.lower() or "$ref" in reason
    assert "declared_field_names" in reason or "top-level 'properties'" in reason
    # Must NOT fall into the generic "empty object" branch for these roots.
    assert "at least one entry in 'properties'" not in reason
    # The rejection message must NAME the offending root key so authors
    # know exactly which composition/$ref key tripped it (PR #315 review).
    offending_key = next(k for k in ("anyOf", "oneOf", "allOf", "$ref") if k in schema)
    assert offending_key in reason


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


def test_pydantic_field_shape_error_wraps_to_skill_validation_failed_error(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """Raw `pydantic.ValidationError` must not escape `load_from_file`.

    Issue #214: when Pydantic rejects a field-shape violation before
    `@model_validator(mode="after")` runs (e.g. a non-integer `version`),
    the resulting `ValidationError` used to propagate unwrapped past
    `load_from_file`. Downstream `SkillLoader` caught it under a broad
    `except Exception` and rendered it as a verbose Pydantic dump instead of
    the curated `file=<path> reason=<human-readable>` format every other
    failure path produces.

    This test locks in the wrap: the exception must be
    `SkillValidationFailedError`, the `file` param must be the offending
    file path, and the `reason` must be a single human-readable line that
    names the offending field and the validation message without any raw
    Pydantic pointer-URL noise.
    """
    path = write_skill_yaml(version="abc")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    assert exc_info.value.params is not None
    dumped = exc_info.value.params.model_dump()
    assert dumped["file"] == str(path)

    reason = _reason(exc_info.value)
    # Single human-readable line — no embedded newline breaks.
    assert "\n" not in reason
    # Names the offending field so operators know which key to fix.
    assert "version" in reason
    # Does NOT include the raw Pydantic traceback pointer noise.
    # Pydantic v2 `ValidationError.__str__` ends each entry with a
    # `[type=..., input_value=..., input_type=...]` trailer followed by
    # `For further information visit https://errors.pydantic.dev/...` —
    # that's exactly the unparseable dump the fix is meant to suppress.
    assert "https://errors.pydantic.dev" not in reason
    assert "For further information visit" not in reason


def test_pydantic_validation_error_reason_is_deterministic_single_line(
    write_skill_yaml: SkillYamlFactory,
) -> None:
    """Shape-level failure reasons must follow the `<field_path>: <msg>` format.

    Locks the `reason` formatter so downstream log-parsers, if any, can rely
    on the `; `-joined `loc: msg` layout rather than Pydantic's free-form
    multi-line dump. Uses a nested field (`docling.ocr`) to prove the dotted
    path comes through.
    """
    path = write_skill_yaml(docling={"ocr": "banana"})

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillYamlSchema.load_from_file(path)

    reason = _reason(exc_info.value)
    assert "\n" not in reason
    # Dotted path for nested field, colon separator, then the message.
    assert "docling.ocr" in reason
    assert ":" in reason

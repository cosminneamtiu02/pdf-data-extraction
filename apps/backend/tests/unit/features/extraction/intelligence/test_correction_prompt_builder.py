"""Unit tests for CorrectionPromptBuilder."""

import json
from types import MappingProxyType

from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)


def test_build_includes_original_prompt_malformed_schema_and_reason() -> None:
    builder = CorrectionPromptBuilder()
    schema = {"type": "object", "required": ["foo"], "properties": {"foo": {"type": "string"}}}

    prompt = builder.build(
        original_prompt="Extract foo from the document.",
        malformed_output='{"foo": 42}',
        output_schema=schema,
        failure_reason="$.foo: 42 is not of type 'string'",
    )

    assert "Extract foo from the document." in prompt
    assert '{"foo": 42}' in prompt
    assert "$.foo: 42 is not of type 'string'" in prompt
    assert json.dumps(schema, indent=2, sort_keys=True) in prompt


def test_build_handles_deep_frozen_schema_with_nested_mappingproxy() -> None:
    """The real ``Skill.output_schema`` is a ``MappingProxyType`` whose nested
    values are themselves ``MappingProxyType`` (see ``deep_freeze_mapping``).

    ``json.dumps(dict(schema), ...)`` only thaws the top level, so any nested
    ``MappingProxyType`` falls through the encoder and raises ``TypeError``.
    The builder must recursively thaw so serialization succeeds and the
    emitted JSON equals what a plain-dict equivalent would produce.
    """
    builder = CorrectionPromptBuilder()
    frozen_schema = MappingProxyType(
        {
            "type": "object",
            "required": ("foo",),
            "properties": MappingProxyType(
                {"foo": MappingProxyType({"type": "string"})},
            ),
        },
    )
    plain_schema = {
        "type": "object",
        "required": ["foo"],
        "properties": {"foo": {"type": "string"}},
    }

    prompt = builder.build(
        original_prompt="Extract foo from the document.",
        malformed_output='{"foo": 42}',
        output_schema=frozen_schema,
        failure_reason="$.foo: 42 is not of type 'string'",
    )

    assert json.dumps(plain_schema, indent=2, sort_keys=True) in prompt

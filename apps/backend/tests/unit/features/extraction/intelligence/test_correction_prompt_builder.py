"""Unit tests for CorrectionPromptBuilder."""

import json

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

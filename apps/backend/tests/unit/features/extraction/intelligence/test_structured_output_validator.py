"""Unit tests for StructuredOutputValidator.

Covers cleanup (markdown fences, prose prefixes, leading-brace fast path),
parsing (JSONDecodeError → retry), schema validation (single + multiple field
errors), retry behavior (success on Nth attempt, exhaustion → StructuredOutputError),
configurability via Settings.structured_output_max_retries, and structlog
emission of `structured_output_retry` events on each retry.

The validator is provider-agnostic: every test passes a plain async stub for
`regeneration_callable` and never touches Ollama, LangExtract, or HTTP.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from structlog.testing import capture_logs

from app.core.config import Settings
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.structured_output_error import (
    StructuredOutputError,
)
from app.features.extraction.intelligence.structured_output_validator import (
    StructuredOutputValidator,
)

_FOO_STRING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["foo"],
    "properties": {"foo": {"type": "string"}},
}


def _build_validator(max_retries: int = 3) -> StructuredOutputValidator:
    return StructuredOutputValidator(
        settings=Settings(structured_output_max_retries=max_retries),
        correction_prompt_builder=CorrectionPromptBuilder(),
    )


def _scripted_callable(
    responses: list[str],
) -> tuple[
    Callable[[str], Awaitable[str]],
    list[str],
]:
    """Return an async callable that yields `responses` in order and records prompts."""
    received_prompts: list[str] = []
    iterator = iter(responses)

    async def _call(prompt: str) -> str:
        received_prompts.append(prompt)
        try:
            return next(iterator)
        except StopIteration:
            pytest.fail("regeneration_callable invoked more times than scripted")

    return _call, received_prompts


def _never_called() -> Callable[[str], Awaitable[str]]:
    async def _call(prompt: str) -> str:
        _ = prompt
        pytest.fail("regeneration_callable should not have been invoked")

    return _call


async def test_clean_json_object_succeeds_in_one_attempt() -> None:
    validator = _build_validator()

    result = await validator.validate_and_retry(
        raw_text='{"foo": "bar"}',
        output_schema=_FOO_STRING_SCHEMA,
        regeneration_callable=_never_called(),
    )

    assert result.data == {"foo": "bar"}
    assert result.attempts == 1
    assert result.raw_output == '{"foo": "bar"}'


async def test_markdown_fenced_with_json_hint_is_stripped() -> None:
    validator = _build_validator()

    result = await validator.validate_and_retry(
        raw_text='```json\n{"foo": "bar"}\n```',
        output_schema=_FOO_STRING_SCHEMA,
        regeneration_callable=_never_called(),
    )

    assert result.data == {"foo": "bar"}
    assert result.attempts == 1


async def test_markdown_fenced_without_language_hint_is_stripped() -> None:
    validator = _build_validator()

    result = await validator.validate_and_retry(
        raw_text='```\n{"foo": "bar"}\n```',
        output_schema=_FOO_STRING_SCHEMA,
        regeneration_callable=_never_called(),
    )

    assert result.data == {"foo": "bar"}
    assert result.attempts == 1


async def test_prose_prefixed_json_is_extracted() -> None:
    validator = _build_validator()

    result = await validator.validate_and_retry(
        raw_text='Here is the output: {"foo": "bar"} hope this helps!',
        output_schema=_FOO_STRING_SCHEMA,
        regeneration_callable=_never_called(),
    )

    assert result.data == {"foo": "bar"}
    assert result.attempts == 1


async def test_leading_brace_fast_path_uses_input_directly() -> None:
    validator = _build_validator()

    result = await validator.validate_and_retry(
        raw_text='{"foo": "bar"}',
        output_schema=_FOO_STRING_SCHEMA,
        regeneration_callable=_never_called(),
    )

    assert result.data == {"foo": "bar"}
    assert result.raw_output == '{"foo": "bar"}'


async def test_invalid_json_first_then_valid_succeeds_in_two_attempts() -> None:
    validator = _build_validator()
    call, prompts = _scripted_callable(['{"foo": "bar"}'])

    result = await validator.validate_and_retry(
        raw_text="not json at all",
        output_schema=_FOO_STRING_SCHEMA,
        regeneration_callable=call,
    )

    assert result.data == {"foo": "bar"}
    assert result.attempts == 2
    assert result.raw_output == '{"foo": "bar"}'
    assert len(prompts) == 1


async def test_schema_violation_triggers_retry_with_path_in_correction_prompt() -> None:
    validator = _build_validator()
    call, prompts = _scripted_callable(['{"foo": "bar"}'])

    result = await validator.validate_and_retry(
        raw_text='{"foo": 42}',
        output_schema=_FOO_STRING_SCHEMA,
        regeneration_callable=call,
    )

    assert result.attempts == 2
    assert len(prompts) == 1
    correction = prompts[0]
    assert '{"foo": 42}' in correction
    # JSONSchema path — `foo` is the failing property
    assert "foo" in correction
    assert "string" in correction


async def test_consistently_invalid_raises_after_four_total_attempts() -> None:
    validator = _build_validator()
    call, prompts = _scripted_callable(["never valid", "never valid", "never valid"])

    with pytest.raises(StructuredOutputError) as excinfo:
        await validator.validate_and_retry(
            raw_text="never valid",
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=call,
        )

    assert len(prompts) == 3
    err = excinfo.value
    assert err.attempts == 4
    assert err.last_raw_output == "never valid"
    assert len(err.failure_reasons) == 4
    assert "last_raw_output" in err.details
    assert err.details["attempts"] == 4


async def test_max_retries_setting_controls_total_attempts() -> None:
    validator = _build_validator(max_retries=5)
    call, prompts = _scripted_callable(["bad"] * 5)

    with pytest.raises(StructuredOutputError) as excinfo:
        await validator.validate_and_retry(
            raw_text="bad",
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=call,
        )

    assert len(prompts) == 5
    assert excinfo.value.attempts == 6


async def test_multiple_missing_fields_aggregate_into_correction_reason() -> None:
    validator = _build_validator()
    schema: dict[str, Any] = {
        "type": "object",
        "required": ["foo", "bar"],
        "properties": {
            "foo": {"type": "string"},
            "bar": {"type": "string"},
        },
    }
    call, prompts = _scripted_callable(['{"foo": "x", "bar": "y"}'])

    result = await validator.validate_and_retry(
        raw_text="{}",
        output_schema=schema,
        regeneration_callable=call,
    )

    assert result.attempts == 2
    assert len(prompts) == 1
    correction = prompts[0]
    assert "foo" in correction
    assert "bar" in correction


async def test_retry_emits_structlog_event_per_attempt() -> None:
    validator = _build_validator()
    call, _prompts = _scripted_callable(['{"foo": "bar"}'])

    with capture_logs() as logs:
        await validator.validate_and_retry(
            raw_text="not json",
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=call,
        )

    retry_events = [e for e in logs if e.get("event") == "structured_output_retry"]
    assert len(retry_events) == 1
    assert retry_events[0]["attempt"] == 1
    assert "reason" in retry_events[0]


async def test_trailing_prose_after_valid_json_succeeds_in_one_attempt() -> None:
    """Regression test: raw_decode must accept trailing content after the JSON object.

    Otherwise model output like '{"foo":"bar"}\\nSome commentary' would burn a retry
    even though the JSON object itself is fully valid against the schema.
    """
    validator = _build_validator()

    result = await validator.validate_and_retry(
        raw_text='{"foo": "bar"}\nSome trailing commentary the model added',
        output_schema=_FOO_STRING_SCHEMA,
        regeneration_callable=_never_called(),
    )

    assert result.data == {"foo": "bar"}
    assert result.attempts == 1


async def test_settings_default_max_retries_is_three() -> None:
    settings = Settings()

    assert settings.structured_output_max_retries == 3


def test_settings_rejects_negative_max_retries() -> None:
    """Regression: a negative max_retries used to silently yield 0 total attempts.

    With `-1`, `max_total_attempts` was `0`, the retry loop body never executed,
    and `StructuredOutputError(attempts=0, failure_reasons=[])` was raised even
    for perfectly valid input. The fix constrains the field at the Settings
    layer so a broken value is rejected at configuration time.
    """
    with pytest.raises(PydanticValidationError):
        Settings(structured_output_max_retries=-1)


async def test_zero_retries_yields_exactly_one_total_attempt_on_valid_input() -> None:
    validator = _build_validator(max_retries=0)

    result = await validator.validate_and_retry(
        raw_text='{"foo": "bar"}',
        output_schema=_FOO_STRING_SCHEMA,
        regeneration_callable=_never_called(),
    )

    assert result.attempts == 1


async def test_zero_retries_raises_immediately_on_invalid_input() -> None:
    validator = _build_validator(max_retries=0)

    with pytest.raises(StructuredOutputError) as excinfo:
        await validator.validate_and_retry(
            raw_text="not json",
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=_never_called(),
        )

    assert excinfo.value.attempts == 1
    assert len(excinfo.value.failure_reasons) == 1

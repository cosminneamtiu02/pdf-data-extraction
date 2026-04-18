"""Unit tests for StructuredOutputValidator.

Covers cleanup (markdown fences, prose prefixes, leading-brace fast path),
parsing (JSONDecodeError → retry), schema validation (single + multiple field
errors), retry behavior (success on Nth attempt, exhaustion → StructuredOutputFailedError),
configurability via Settings.structured_output_max_retries, and structlog
emission of `structured_output_retry` events on each retry.

The validator is provider-agnostic: every test passes a plain async stub for
`regeneration_callable` and never touches Ollama, LangExtract, or HTTP.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError as PydanticValidationError
from structlog.testing import capture_logs

from app.core.config import Settings
from app.exceptions import StructuredOutputFailedError
from app.features.extraction.intelligence import (
    structured_output_validator as sov_module,
)
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.structured_output_validator import (
    StructuredOutputValidator,
    _clean,
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

    with capture_logs() as logs, pytest.raises(StructuredOutputFailedError) as excinfo:
        await validator.validate_and_retry(
            raw_text="never valid",
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=call,
        )

    assert len(prompts) == 3
    assert excinfo.value.code == "STRUCTURED_OUTPUT_FAILED"
    assert excinfo.value.http_status == 502
    failed_events = [e for e in logs if e.get("event") == "structured_output_failed"]
    assert len(failed_events) == 1
    event = failed_events[0]
    assert event["attempts"] == 4
    # Raw output and value-laden failure_reasons are removed — logs only
    # carry sanitized cause codes now.
    assert "last_raw_output" not in event
    assert "failure_reasons" not in event
    assert len(event["causes"]) == 4


async def test_max_retries_setting_controls_total_attempts() -> None:
    validator = _build_validator(max_retries=5)
    call, prompts = _scripted_callable(["bad"] * 5)

    with capture_logs() as logs, pytest.raises(StructuredOutputFailedError):
        await validator.validate_and_retry(
            raw_text="bad",
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=call,
        )

    assert len(prompts) == 5
    failed_events = [e for e in logs if e.get("event") == "structured_output_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["attempts"] == 6


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
    assert "cause" in retry_events[0]


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

    with capture_logs() as logs, pytest.raises(StructuredOutputFailedError):
        await validator.validate_and_retry(
            raw_text="not json",
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=_never_called(),
        )

    failed_events = [e for e in logs if e.get("event") == "structured_output_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["attempts"] == 1
    assert len(failed_events[0]["causes"]) == 1
    assert "last_raw_output" not in failed_events[0]


async def test_structured_output_failed_error_is_domain_error_subclass() -> None:
    from app.exceptions.base import DomainError

    assert issubclass(StructuredOutputFailedError, DomainError)
    assert StructuredOutputFailedError.code == "STRUCTURED_OUTPUT_FAILED"
    assert StructuredOutputFailedError.http_status == 502


async def test_structured_output_failed_log_omits_raw_output_and_field_values() -> None:
    """Logs must not leak raw model output, PDF content, or validation-error values.

    Regression guard: JSONSchema Draft7 validation error messages include the
    offending value verbatim (e.g. "'SSN-999-12-3456' is not of type 'integer'").
    The repo's redaction keys in Settings only strip exact keys like `raw_output`
    and `extracted_value`; the `last_raw_output` key and embedded error strings
    slip past the filter. The only safe contract is: never log raw output, and
    log sanitized path-only codes instead of raw failure reasons.
    """
    validator = _build_validator(max_retries=1)

    sensitive_value = "SSN-999-12-3456"
    raw_with_sensitive = '{"foo": "' + sensitive_value + '"}'
    # Schema wants integer; raw has string → validation error text embeds the
    # offending string value into its message.
    schema_requiring_int: dict[str, Any] = {
        "type": "object",
        "required": ["foo"],
        "properties": {"foo": {"type": "integer"}},
    }
    call, _prompts = _scripted_callable([raw_with_sensitive])

    with capture_logs() as logs, pytest.raises(StructuredOutputFailedError):
        await validator.validate_and_retry(
            raw_text=raw_with_sensitive,
            output_schema=schema_requiring_int,
            regeneration_callable=call,
        )

    all_log_text = "\n".join(repr(e) for e in logs)
    assert sensitive_value not in all_log_text, f"Sensitive value leaked into logs: {all_log_text}"

    failed = next(e for e in logs if e.get("event") == "structured_output_failed")
    # Raw output is removed entirely — no field to leak from.
    assert "last_raw_output" not in failed
    # failure_reasons (which contained value-laden error messages) is replaced
    # with `causes`, a list of sanitized path-only codes.
    assert "failure_reasons" not in failed
    assert "causes" in failed
    assert all(isinstance(c, str) for c in failed["causes"])
    # Schema-violation codes look like "schema_violation:<json_paths>" —
    # containing only the JSON paths, never user values.
    assert any("schema_violation" in c for c in failed["causes"])


def test_clean_strips_trailing_fence_when_no_opening_fence_present() -> None:
    """Regression: trailing ``` must be stripped even without an opening fence.

    Before the fix, `_clean` only stripped a trailing fence inside the branch
    that detected an opening fence, so output like `{...}\\n``` left the
    closing backticks in the cleaned text. `raw_decode` happens to tolerate
    the trailing noise today, but the contract of `_clean` is to hand JSON
    parsing a fence-free string — relying on `raw_decode`'s leniency is
    fragile and hides the intent.
    """
    raw = '{"foo": "bar"}\n```'

    cleaned = _clean(raw)

    assert cleaned == '{"foo": "bar"}'


def test_clean_is_noop_when_no_fences_present() -> None:
    """Regression guard: plain JSON must pass through `_clean` unchanged."""
    raw = '{"foo": "bar"}'

    cleaned = _clean(raw)

    assert cleaned == '{"foo": "bar"}'


def test_clean_strips_both_opening_and_trailing_fence() -> None:
    """Regression guard: fully-fenced input keeps its previous behavior."""
    raw = '```json\n{"foo": "bar"}\n```'

    cleaned = _clean(raw)

    assert cleaned == '{"foo": "bar"}'


def test_clean_strips_trailing_fence_with_surrounding_whitespace() -> None:
    """Trailing fence followed by whitespace must also be stripped."""
    raw = '{"foo": "bar"}\n```\n   '

    cleaned = _clean(raw)

    assert cleaned == '{"foo": "bar"}'


async def test_structured_output_retry_log_uses_sanitized_cause_field() -> None:
    """The per-attempt retry log emits `cause`, not `reason`.

    Regression guard: the previous `reason=<full_error_text>` field leaked
    validation-error messages (which embed user values) into logs. The new
    contract emits `cause` with a sanitized category code ("schema_violation",
    "json_parse_error", etc.) plus optional path metadata.
    """
    validator = _build_validator()
    call, _prompts = _scripted_callable(['{"foo": "bar"}'])

    with capture_logs() as logs:
        await validator.validate_and_retry(
            raw_text="not json at all",
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=call,
        )

    retry = next(e for e in logs if e.get("event") == "structured_output_retry")
    assert "cause" in retry
    assert isinstance(retry["cause"], str)
    # Legacy field name must be gone.
    assert "reason" not in retry


async def test_draft7_validator_is_built_once_across_retries_for_same_schema() -> None:
    """Regression guard for issue #233: Draft7Validator must be compiled once per schema.

    Under default settings the validator runs 4 total attempts. This test
    scripts valid JSON that violates the schema so every attempt reaches
    ``_validate`` (i.e. genuinely exercises the schema-validation retry path
    the cache is meant to amortize). It patches ``Draft7Validator`` and
    asserts the constructor is invoked exactly once across all retries within
    a single ``validate_and_retry`` call for the same ``output_schema``
    identity.
    """
    validator = _build_validator()
    # Four attempts: all valid JSON, all violating `_FOO_STRING_SCHEMA` (foo
    # must be a string). Each attempt reaches `_validate` → exercises the
    # schema-validation retry path, which is exactly what the cache amortizes.
    call, _prompts = _scripted_callable(['{"foo": 2}', '{"foo": 3}', '{"foo": 4}'])

    with (
        patch.object(
            sov_module,
            "Draft7Validator",
            wraps=sov_module.Draft7Validator,
        ) as mock_validator,
        pytest.raises(StructuredOutputFailedError),
    ):
        await validator.validate_and_retry(
            raw_text='{"foo": 1}',
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=call,
        )

    # Four attempts over the same schema identity → exactly ONE constructor call.
    assert mock_validator.call_count == 1


async def test_draft7_validator_is_not_built_when_every_attempt_fails_json_parsing() -> None:
    """Lazy-compile guarantee: no Draft7Validator is built when parsing never succeeds.

    Building ``Draft7Validator`` walks the schema to normalise refs and
    precompile per-keyword validators — a real cost. Paying that cost for a
    call whose every attempt fails JSON parsing (so ``_validate`` is never
    reached) would be a pointless performance regression. This test scripts
    outputs that all fail parsing and asserts the constructor is never
    invoked.
    """
    validator = _build_validator()
    # Four attempts, all pure parse-failures (not JSON at all).
    call, _prompts = _scripted_callable(["not json", "still not json", "never valid"])

    with (
        patch.object(
            sov_module,
            "Draft7Validator",
            wraps=sov_module.Draft7Validator,
        ) as mock_validator,
        pytest.raises(StructuredOutputFailedError),
    ):
        await validator.validate_and_retry(
            raw_text="not json",
            output_schema=_FOO_STRING_SCHEMA,
            regeneration_callable=call,
        )

    # Parsing never succeeds → _validate is never called → no Draft7Validator built.
    assert mock_validator.call_count == 0


async def test_draft7_validator_is_reused_across_separate_validate_calls() -> None:
    """The compiled validator must persist across distinct `validate_and_retry` calls.

    Two sequential calls against the same `output_schema` identity share one
    Draft7Validator — not two. This is the sustained-load savings the issue
    describes: concurrent extractions and per-extraction retries all amortize
    onto a single compilation per schema per validator instance.
    """
    validator = _build_validator()

    with patch.object(
        sov_module,
        "Draft7Validator",
        wraps=sov_module.Draft7Validator,
    ) as mock_validator:
        for _ in range(3):
            result = await validator.validate_and_retry(
                raw_text='{"foo": "bar"}',
                output_schema=_FOO_STRING_SCHEMA,
                regeneration_callable=_never_called(),
            )
            assert result.data == {"foo": "bar"}

    assert mock_validator.call_count == 1


async def test_distinct_schemas_each_compile_once() -> None:
    """Two different schema objects → two compiled validators, each reused.

    The cache is keyed per schema identity: distinct schemas must not collide,
    and each schema's compiled validator must still be reused on repeat calls.
    """
    validator = _build_validator()
    schema_a: dict[str, Any] = {
        "type": "object",
        "required": ["foo"],
        "properties": {"foo": {"type": "string"}},
    }
    schema_b: dict[str, Any] = {
        "type": "object",
        "required": ["bar"],
        "properties": {"bar": {"type": "integer"}},
    }

    with patch.object(
        sov_module,
        "Draft7Validator",
        wraps=sov_module.Draft7Validator,
    ) as mock_validator:
        for _ in range(2):
            await validator.validate_and_retry(
                raw_text='{"foo": "bar"}',
                output_schema=schema_a,
                regeneration_callable=_never_called(),
            )
            await validator.validate_and_retry(
                raw_text='{"bar": 7}',
                output_schema=schema_b,
                regeneration_callable=_never_called(),
            )

    # Two distinct schemas compiled once each → exactly 2 constructor calls.
    assert mock_validator.call_count == 2

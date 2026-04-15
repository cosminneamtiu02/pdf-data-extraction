"""StructuredOutputValidator: clean → parse → validate → retry, provider-agnostic.

This class compensates for LLMs that lack native controlled generation by
applying a conservative cleanup pass to the raw model text, parsing the result
as JSON, validating the parsed object against the skill's JSONSchema, and
retrying via a caller-supplied regeneration callable on any failure.

The validator is deliberately ignorant of the model vendor: it accepts a
`Callable[[str], Awaitable[str]]` and stays free of Ollama, LangExtract, and
HTTP imports. That keeps the same retry loop reusable against any future
provider that can produce text.

Cleanup is deliberately conservative — strip markdown code fences and locate
the first `{...}` substring via `json.JSONDecoder.raw_decode`. Anything fancier
(quote-flipping, brace-balancing, comma-trimming) is rejected to avoid silent
correctness bugs.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError

from app.core.config import Settings
from app.exceptions import StructuredOutputFailedError
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.generation_result import GenerationResult

_logger = structlog.get_logger(__name__)

_FENCE_LANGUAGE_PREFIXES: tuple[str, ...] = ("```json", "```JSON", "```")

_RAW_OUTPUT_LOG_TRUNCATION: int = 500


class StructuredOutputValidator:
    def __init__(
        self,
        settings: Settings,
        correction_prompt_builder: CorrectionPromptBuilder,
    ) -> None:
        self._settings = settings
        self._correction_prompt_builder = correction_prompt_builder

    async def validate_and_retry(
        self,
        raw_text: str,
        output_schema: dict[str, Any],
        regeneration_callable: Callable[[str], Awaitable[str]],
        original_prompt: str = "",
    ) -> GenerationResult:
        max_total_attempts = self._settings.structured_output_max_retries + 1
        current_text = raw_text
        failure_reasons: list[str] = []

        for attempt in range(1, max_total_attempts + 1):
            failure_reason: str | None
            cleaned = _clean(current_text)
            parsed, parse_error = _try_parse(cleaned)
            if parse_error is not None:
                failure_reason = parse_error
            else:
                validation_error = _validate(parsed, output_schema)
                if validation_error is None:
                    return GenerationResult(
                        data=parsed,
                        attempts=attempt,
                        raw_output=current_text,
                    )
                failure_reason = validation_error

            failure_reasons.append(failure_reason)
            if attempt == max_total_attempts:
                break
            _logger.info(
                "structured_output_retry",
                attempt=attempt,
                reason=failure_reason,
            )
            correction = self._correction_prompt_builder.build(
                original_prompt=original_prompt,
                malformed_output=current_text,
                output_schema=output_schema,
                failure_reason=failure_reason,
            )
            current_text = await regeneration_callable(correction)

        _logger.error(
            "structured_output_failed",
            attempts=max_total_attempts,
            failure_reasons=failure_reasons,
            last_raw_output=current_text[:_RAW_OUTPUT_LOG_TRUNCATION],
        )
        raise StructuredOutputFailedError


def _clean(raw_text: str) -> str:
    text = raw_text.strip()
    for prefix in _FENCE_LANGUAGE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip("\n").lstrip()
            if text.endswith("```"):
                text = text[: -len("```")].rstrip()
            break
    return text.strip()


def _try_parse(cleaned: str) -> tuple[dict[str, Any], None] | tuple[None, str]:
    brace_index = 0 if cleaned.startswith("{") else cleaned.find("{")
    if brace_index == -1:
        return None, "no JSON object substring found in raw text"
    decoder = json.JSONDecoder()
    try:
        value, _end = decoder.raw_decode(cleaned[brace_index:])
    except json.JSONDecodeError as exc:
        return None, f"json.loads failed: {exc.msg} (line {exc.lineno}, col {exc.colno})"
    return _coerce_object(value)


def _coerce_object(value: object) -> tuple[dict[str, Any], None] | tuple[None, str]:
    if not isinstance(value, dict):
        return None, f"parsed JSON is {type(value).__name__}, expected object"
    typed: dict[str, Any] = {str(k): v for k, v in value.items()}  # type: ignore[misc]  # value is dict[Unknown, Unknown] from json.loads — narrow to str keys
    return typed, None


def _validate(
    parsed: dict[str, Any],
    output_schema: dict[str, Any],
) -> str | None:
    validator = Draft7Validator(output_schema)
    raw_errors: Any = validator.iter_errors(parsed)  # pyright: ignore[reportUnknownMemberType]  # jsonschema's overloaded iter_errors signature is partially typed in the stubs
    errors: list[ValidationError] = sorted(
        raw_errors,
        key=lambda e: [str(p) for p in e.absolute_path],
    )
    if not errors:
        return None
    return "; ".join(_format_error(e) for e in errors)


def _format_error(error: ValidationError) -> str:
    return f"{error.json_path}: {error.message}"

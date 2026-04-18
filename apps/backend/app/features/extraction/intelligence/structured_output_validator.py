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

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from jsonschema import Draft7Validator

from app.exceptions import StructuredOutputFailedError
from app.features.extraction.intelligence.generation_result import GenerationResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from jsonschema.exceptions import ValidationError

    from app.core.config import Settings
    from app.features.extraction.intelligence.correction_prompt_builder import (
        CorrectionPromptBuilder,
    )

_logger = structlog.get_logger(__name__)

_FENCE_LANGUAGE_PREFIXES: tuple[str, ...] = ("```json", "```JSON", "```")

# Soft cap on the compiled-validator cache. Real call sites pass process-
# lifetime schemas (``LANGEXTRACT_WRAPPER_SCHEMA`` + one ``Skill.output_schema``
# per installed skill), so cache size is bounded by the skills manifest. The
# cap is a canary: crossing it means a caller is passing per-request dicts,
# violating the cache's identity-stability contract, and we want an operator-
# visible warning rather than silent memory growth.
_COMPILED_VALIDATOR_CACHE_SOFT_CAP: int = 128


# Each parse/validate helper returns either a successful parse, or a failure
# represented as `(log_cause, llm_reason)`:
#   - `log_cause`  — sanitized category code safe to emit to structlog. For
#                    schema violations, includes only the JSON paths, never
#                    the offending values.
#   - `llm_reason` — full error text (may embed offending values). Passed to
#                    `CorrectionPromptBuilder` so the LLM can self-correct.
#                    MUST NOT be logged.
_FailurePair = tuple[str, str]


class StructuredOutputValidator:
    def __init__(
        self,
        settings: Settings,
        correction_prompt_builder: CorrectionPromptBuilder,
    ) -> None:
        self._settings = settings
        self._correction_prompt_builder = correction_prompt_builder
        # Compiled-validator cache keyed by ``id(output_schema)``. Building a
        # ``Draft7Validator`` walks the schema to normalise refs and precompile
        # per-keyword validators (issue #233); the cost compounds across
        # per-extraction retries and across concurrent extractions. Both real
        # schema sources — ``LANGEXTRACT_WRAPPER_SCHEMA`` (module constant) and
        # ``Skill.output_schema`` (``MappingProxyType`` held by the long-lived
        # ``Skill`` instance) — have process-lifetime stability, so id-keying is
        # safe. We keep the schema object itself in the cache value so the id
        # cannot be recycled onto a different object while the cache is live.
        # The cache is expected to stay small (one entry per distinct schema,
        # i.e. one per skill plus the LangExtract wrapper). We soft-cap and
        # warn if ever exceeded, as a canary for a caller that violates the
        # "process-lifetime schema identity" contract with per-request dicts.
        self._compiled_validators: dict[int, tuple[Mapping[str, Any], Draft7Validator]] = {}

    def _get_compiled_validator(self, output_schema: Mapping[str, Any]) -> Draft7Validator:
        schema_id = id(output_schema)
        cached = self._compiled_validators.get(schema_id)
        if cached is not None:
            return cached[1]
        compiled = Draft7Validator(output_schema)
        self._compiled_validators[schema_id] = (output_schema, compiled)
        if len(self._compiled_validators) > _COMPILED_VALIDATOR_CACHE_SOFT_CAP:
            _logger.warning(
                "structured_output_validator_cache_soft_cap_exceeded",
                cache_size=len(self._compiled_validators),
                soft_cap=_COMPILED_VALIDATOR_CACHE_SOFT_CAP,
            )
        return compiled

    async def validate_and_retry(
        self,
        raw_text: str,
        output_schema: Mapping[str, Any],
        regeneration_callable: Callable[[str], Awaitable[str]],
        original_prompt: str = "",
    ) -> GenerationResult:
        max_total_attempts = self._settings.structured_output_max_retries + 1
        current_text = raw_text
        log_causes: list[str] = []
        # Lazily compile the Draft7Validator only on the first attempt that
        # yields a parsed object — parsing can fail for every attempt (e.g. the
        # model never emits JSON), in which case we should not pay the
        # compilation cost at all. Once compiled, the instance is cached for
        # reuse across subsequent schema-validation retries within this call
        # AND across future `validate_and_retry` invocations.
        compiled_validator: Draft7Validator | None = None

        for attempt in range(1, max_total_attempts + 1):
            cleaned = _clean(current_text)
            parsed, parse_failure = _try_parse(cleaned)
            failure: _FailurePair | None
            if parse_failure is not None:
                failure = parse_failure
            else:
                if compiled_validator is None:
                    compiled_validator = self._get_compiled_validator(output_schema)
                failure = _validate(parsed, compiled_validator)
                if failure is None:
                    return GenerationResult(
                        data=parsed,
                        attempts=attempt,
                        raw_output=current_text,
                    )

            log_cause, llm_reason = failure
            log_causes.append(log_cause)
            if attempt == max_total_attempts:
                break
            _logger.info(
                "structured_output_retry",
                attempt=attempt,
                cause=log_cause,
            )
            correction = self._correction_prompt_builder.build(
                original_prompt=original_prompt,
                malformed_output=current_text,
                output_schema=output_schema,
                failure_reason=llm_reason,
            )
            current_text = await regeneration_callable(correction)

        _logger.error(
            "structured_output_failed",
            attempts=max_total_attempts,
            causes=log_causes,
        )
        raise StructuredOutputFailedError


def _clean(raw_text: str) -> str:
    # Three sequential, independent passes. The opening-fence and trailing-fence
    # passes are self-contained: one inspects the start of the string and the
    # other inspects the end, so neither depends on whether the other matched.
    # They are applied in this order simply as the cleanup flow.
    text = raw_text.strip()
    text = _strip_opening_fence(text)
    text = _strip_trailing_fence(text)
    return text.strip()


def _strip_opening_fence(text: str) -> str:
    for prefix in _FENCE_LANGUAGE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :].lstrip("\n").lstrip()
    return text


def _strip_trailing_fence(text: str) -> str:
    stripped = text.rstrip()
    if stripped.endswith("```"):
        return stripped[: -len("```")].rstrip()
    return text


def _try_parse(cleaned: str) -> tuple[dict[str, Any], None] | tuple[None, _FailurePair]:
    brace_index = 0 if cleaned.startswith("{") else cleaned.find("{")
    if brace_index == -1:
        return None, ("no_json_object", "no JSON object substring found in raw text")
    decoder = json.JSONDecoder()
    try:
        value, _end = decoder.raw_decode(cleaned[brace_index:])
    except json.JSONDecodeError as exc:
        return None, (
            "json_parse_error",
            f"JSON decode failed: {exc.msg} (line {exc.lineno}, col {exc.colno})",
        )
    return _coerce_object(value)


def _coerce_object(
    value: object,
) -> tuple[dict[str, Any], None] | tuple[None, _FailurePair]:
    if not isinstance(value, dict):
        return None, (
            "not_object",
            f"parsed JSON is {type(value).__name__}, expected object",
        )
    typed: dict[str, Any] = {str(k): v for k, v in value.items()}  # type: ignore[misc]  # value is dict[Unknown, Unknown] from json.loads — narrow to str keys
    return typed, None


def _validate(
    parsed: dict[str, Any],
    compiled_validator: Draft7Validator,
) -> _FailurePair | None:
    raw_errors: Any = compiled_validator.iter_errors(parsed)  # pyright: ignore[reportUnknownMemberType]  # jsonschema's overloaded iter_errors signature is partially typed in the stubs
    errors: list[ValidationError] = sorted(
        raw_errors,
        key=lambda e: [str(p) for p in e.absolute_path],
    )
    if not errors:
        return None
    # For the log: path-only summary — never user values. Duplicate paths are
    # collapsed so the cause string stays stable when multiple errors target
    # the same field.
    paths = sorted({e.json_path for e in errors})
    log_cause = f"schema_violation:{','.join(paths)}"
    # For the LLM: full error text so the model can self-correct. This string
    # may embed user values from the parsed output and MUST stay out of logs.
    llm_reason = "; ".join(f"{e.json_path}: {e.message}" for e in errors)
    return (log_cause, llm_reason)

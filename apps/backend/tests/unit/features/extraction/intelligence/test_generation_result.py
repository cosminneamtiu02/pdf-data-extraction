"""Unit tests for GenerationResult frozen dataclass."""

import dataclasses

import pytest

from app.features.extraction.intelligence.generation_result import GenerationResult


def test_generation_result_exposes_fields() -> None:
    result = GenerationResult(data={"foo": "bar"}, attempts=1, raw_output='{"foo": "bar"}')

    assert result.data == {"foo": "bar"}
    assert result.attempts == 1
    assert result.raw_output == '{"foo": "bar"}'


def test_generation_result_is_frozen() -> None:
    result = GenerationResult(data={"foo": "bar"}, attempts=1, raw_output='{"foo": "bar"}')

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.attempts = 2  # type: ignore[misc]  # frozen dataclass — assignment is intentional


def test_generation_result_rejects_unknown_fields() -> None:
    with pytest.raises(TypeError):
        GenerationResult(  # type: ignore[call-arg]  # extra kwarg is intentional
            data={"foo": "bar"},
            attempts=1,
            raw_output='{"foo": "bar"}',
            unexpected="boom",
        )

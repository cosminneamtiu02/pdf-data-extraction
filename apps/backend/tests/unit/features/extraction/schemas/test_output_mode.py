"""Unit tests for OutputMode."""

import pytest
from pydantic import BaseModel

from app.features.extraction.schemas.output_mode import OutputMode


def test_output_mode_values_are_the_three_declared_strings() -> None:
    assert OutputMode.JSON_ONLY.value == "JSON_ONLY"
    assert OutputMode.PDF_ONLY.value == "PDF_ONLY"
    assert OutputMode.BOTH.value == "BOTH"


def test_output_mode_constructs_from_each_string_value() -> None:
    assert OutputMode("JSON_ONLY") is OutputMode.JSON_ONLY
    assert OutputMode("PDF_ONLY") is OutputMode.PDF_ONLY
    assert OutputMode("BOTH") is OutputMode.BOTH


def test_output_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="XML"):
        OutputMode("XML")


def test_output_mode_serializes_as_plain_string_inside_a_pydantic_model() -> None:
    class _Wrapper(BaseModel):
        mode: OutputMode

    wrapped = _Wrapper(mode=OutputMode.JSON_ONLY)

    assert wrapped.model_dump_json() == '{"mode":"JSON_ONLY"}'
    assert _Wrapper.model_validate_json('{"mode":"JSON_ONLY"}').mode is OutputMode.JSON_ONLY

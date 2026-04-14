"""Unit tests for FieldStatus."""

import pytest
from pydantic import BaseModel

from app.features.extraction.schemas.field_status import FieldStatus


def test_field_status_values_are_lowercase_strings() -> None:
    assert FieldStatus.extracted.value == "extracted"
    assert FieldStatus.failed.value == "failed"


def test_field_status_constructs_from_string_values() -> None:
    assert FieldStatus("extracted") is FieldStatus.extracted
    assert FieldStatus("failed") is FieldStatus.failed


def test_field_status_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="other"):
        FieldStatus("other")


def test_field_status_serializes_as_plain_string_inside_a_pydantic_model() -> None:
    class _Wrapper(BaseModel):
        status: FieldStatus

    wrapped = _Wrapper(status=FieldStatus.extracted)

    assert wrapped.model_dump_json() == '{"status":"extracted"}'
    assert _Wrapper.model_validate_json('{"status":"failed"}').status is FieldStatus.failed

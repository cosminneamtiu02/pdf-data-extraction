"""Unit tests for ExtractResponse."""

import pytest
from pydantic import ValidationError

from app.features.extraction.schemas.extract_response import ExtractResponse
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
from app.features.extraction.schemas.field_status import FieldStatus


def _valid_metadata() -> ExtractionMetadata:
    return ExtractionMetadata(
        page_count=3,
        duration_ms=1200,
        attempts_per_field={"invoice_number": 1},
        parser_warnings=[],
    )


def _valid_field() -> ExtractedField:
    return ExtractedField(
        name="invoice_number",
        value="INV-001",
        status=FieldStatus.extracted,
        source="document",
        grounded=True,
        bbox_refs=[],
    )


def test_extract_response_full_construction_round_trips() -> None:
    original = ExtractResponse(
        skill_name="invoice",
        skill_version=1,
        fields={"invoice_number": _valid_field()},
        metadata=_valid_metadata(),
    )

    parsed = ExtractResponse.model_validate_json(original.model_dump_json())

    assert parsed == original


def test_extract_response_top_level_keys_are_exactly_four() -> None:
    response = ExtractResponse(
        skill_name="invoice",
        skill_version=1,
        fields={"invoice_number": _valid_field()},
        metadata=_valid_metadata(),
    )

    dumped = response.model_dump()

    assert set(dumped.keys()) == {"skill_name", "skill_version", "fields", "metadata"}


def test_extract_response_rejects_latest_string_for_skill_version() -> None:
    with pytest.raises(ValidationError, match="skill_version"):
        ExtractResponse(
            skill_name="invoice",
            skill_version="latest",  # type: ignore[arg-type]  # intentionally wrong
            fields={},
            metadata=_valid_metadata(),
        )


def test_extract_response_empty_fields_dict_accepted_at_schema_level() -> None:
    response = ExtractResponse(
        skill_name="invoice",
        skill_version=1,
        fields={},
        metadata=_valid_metadata(),
    )

    assert response.fields == {}

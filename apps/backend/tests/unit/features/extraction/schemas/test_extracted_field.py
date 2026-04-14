"""Unit tests for ExtractedField."""

import pytest
from pydantic import ValidationError

from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.field_status import FieldStatus


def test_extracted_field_full_construction_serializes_bbox_list() -> None:
    field = ExtractedField(
        name="invoice_number",
        value="INV-001",
        status=FieldStatus.extracted,
        source="document",
        grounded=True,
        bbox_refs=[BoundingBoxRef(page=1, x0=0.0, y0=0.0, x1=10.0, y1=10.0)],
    )

    dumped = field.model_dump()

    assert dumped["name"] == "invoice_number"
    assert dumped["value"] == "INV-001"
    assert dumped["status"] == "extracted"
    assert dumped["source"] == "document"
    assert dumped["grounded"] is True
    assert len(dumped["bbox_refs"]) == 1


def test_extracted_field_failed_with_none_value_constructs() -> None:
    field = ExtractedField(
        name="x",
        value=None,
        status=FieldStatus.failed,
        source="document",
        grounded=False,
        bbox_refs=[],
    )

    assert field.value is None
    assert field.bbox_refs == []


def test_extracted_field_bbox_refs_defaults_to_empty_list_when_omitted() -> None:
    field = ExtractedField(
        name="x",
        value=None,
        status=FieldStatus.failed,
        source="document",
        grounded=False,
    )

    assert field.bbox_refs == []


def test_extracted_field_default_bbox_lists_are_not_shared_between_instances() -> None:
    a = ExtractedField(
        name="a",
        value=None,
        status=FieldStatus.failed,
        source="document",
        grounded=False,
    )
    b = ExtractedField(
        name="b",
        value=None,
        status=FieldStatus.failed,
        source="document",
        grounded=False,
    )

    a.bbox_refs.append(BoundingBoxRef(page=1, x0=0.0, y0=0.0, x1=1.0, y1=1.0))

    assert b.bbox_refs == []


def test_extracted_field_round_trips_through_json_with_bbox_refs() -> None:
    original = ExtractedField(
        name="invoice_number",
        value="INV-001",
        status=FieldStatus.extracted,
        source="document",
        grounded=True,
        bbox_refs=[BoundingBoxRef(page=1, x0=0.0, y0=0.0, x1=10.0, y1=10.0)],
    )

    parsed = ExtractedField.model_validate_json(original.model_dump_json())

    assert parsed == original
    assert len(parsed.bbox_refs) == 1


def test_extracted_field_rejects_invalid_source_literal() -> None:
    with pytest.raises(ValidationError, match="source"):
        ExtractedField(
            name="x",
            value=None,
            status=FieldStatus.failed,
            source="hallucinated",  # type: ignore[arg-type]  # intentionally wrong for the test
            grounded=False,
            bbox_refs=[],
        )


@pytest.mark.parametrize(
    "value",
    ["a string", 42, 3.14, ["a", "b"], {"k": "v"}, None],
)
def test_extracted_field_accepts_heterogeneous_value_types(value: object) -> None:
    field = ExtractedField(
        name="x",
        value=value,
        status=FieldStatus.extracted,
        source="document",
        grounded=True,
        bbox_refs=[],
    )

    assert field.value == value

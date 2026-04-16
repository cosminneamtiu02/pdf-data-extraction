"""Unit tests for ExtractionResult dataclass (PDFX-E006-F002)."""

import pytest

from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.schemas.extract_response import ExtractResponse
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
from app.features.extraction.schemas.field_status import FieldStatus


def _sample_response() -> ExtractResponse:
    field = ExtractedField(
        name="number",
        value="INV-001",
        status=FieldStatus.extracted,
        source="document",
        grounded=True,
        bbox_refs=[],
    )
    metadata = ExtractionMetadata(
        page_count=2,
        duration_ms=1500,
        attempts_per_field={"number": 1},
    )
    return ExtractResponse(
        skill_name="invoice",
        skill_version=1,
        fields={"number": field},
        metadata=metadata,
    )


def test_extraction_result_with_response_and_none_pdf() -> None:
    result = ExtractionResult(
        response=_sample_response(),
        annotated_pdf_bytes=None,
    )
    assert result.response.skill_name == "invoice"
    assert result.annotated_pdf_bytes is None


def test_extraction_result_with_response_and_pdf_bytes() -> None:
    pdf_data = b"%PDF-1.4 annotated"
    result = ExtractionResult(
        response=_sample_response(),
        annotated_pdf_bytes=pdf_data,
    )
    assert result.annotated_pdf_bytes == pdf_data
    assert result.response.skill_version == 1


def test_extraction_result_is_frozen() -> None:
    result = ExtractionResult(
        response=_sample_response(),
        annotated_pdf_bytes=None,
    )
    with pytest.raises(AttributeError):
        result.annotated_pdf_bytes = b"nope"  # type: ignore[misc]

"""Unit tests for ExtractRequest."""

from app.features.extraction.schemas.extract_request import ExtractRequest
from app.features.extraction.schemas.output_mode import OutputMode


def test_extract_request_integer_skill_version_accepted() -> None:
    request = ExtractRequest(
        skill_name="invoice",
        skill_version="1",
        output_mode=OutputMode.JSON_ONLY,
    )

    assert request.skill_name == "invoice"
    assert request.skill_version == "1"
    assert request.output_mode is OutputMode.JSON_ONLY


def test_extract_request_latest_alias_accepted() -> None:
    request = ExtractRequest(
        skill_name="invoice",
        skill_version="latest",
        output_mode=OutputMode.BOTH,
    )

    assert request.skill_version == "latest"


def test_extract_request_round_trips_through_json() -> None:
    original = ExtractRequest(
        skill_name="invoice",
        skill_version="3",
        output_mode=OutputMode.PDF_ONLY,
    )

    parsed = ExtractRequest.model_validate_json(original.model_dump_json())

    assert parsed == original

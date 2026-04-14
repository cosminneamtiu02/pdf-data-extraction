"""Unit tests for ExtractRequest."""

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize(
    "skill_version",
    ["1", "2", "42", "999", "latest"],
)
def test_extract_request_valid_skill_versions_accepted(skill_version: str) -> None:
    request = ExtractRequest(
        skill_name="invoice",
        skill_version=skill_version,
        output_mode=OutputMode.JSON_ONLY,
    )

    assert request.skill_version == skill_version


@pytest.mark.parametrize(
    "skill_version",
    ["", "banana", "v2", "-1", "0", "01", "1.0", "latest ", " latest", "LATEST"],
)
def test_extract_request_invalid_skill_versions_rejected(skill_version: str) -> None:
    with pytest.raises(ValidationError, match="skill_version"):
        ExtractRequest(
            skill_name="invoice",
            skill_version=skill_version,
            output_mode=OutputMode.JSON_ONLY,
        )


def test_extract_request_round_trips_through_json() -> None:
    original = ExtractRequest(
        skill_name="invoice",
        skill_version="3",
        output_mode=OutputMode.PDF_ONLY,
    )

    parsed = ExtractRequest.model_validate_json(original.model_dump_json())

    assert parsed == original

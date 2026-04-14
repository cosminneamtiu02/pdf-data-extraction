"""Unit tests for ExtractionMetadata."""

from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata


def test_extraction_metadata_full_round_trip() -> None:
    original = ExtractionMetadata(
        page_count=3,
        duration_ms=1200,
        attempts_per_field={"a": 1, "b": 2},
        parser_warnings=["low OCR confidence"],
    )

    parsed = ExtractionMetadata.model_validate_json(original.model_dump_json())

    assert parsed == original


def test_extraction_metadata_empty_collections_allowed() -> None:
    meta = ExtractionMetadata(
        page_count=0,
        duration_ms=0,
        attempts_per_field={},
        parser_warnings=[],
    )

    assert meta.page_count == 0
    assert meta.attempts_per_field == {}
    assert meta.parser_warnings == []


def test_extraction_metadata_parser_warnings_defaults_to_empty_list() -> None:
    meta = ExtractionMetadata(
        page_count=1,
        duration_ms=100,
        attempts_per_field={},
    )

    assert meta.parser_warnings == []

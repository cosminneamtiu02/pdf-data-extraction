"""Unit tests for RawExtraction — the feature-owned shape downstream of LangExtract."""

import dataclasses

import pytest

from app.features.extraction.extraction.raw_extraction import RawExtraction


def test_raw_extraction_is_frozen_and_rejects_assignment() -> None:
    raw = RawExtraction(
        field_name="name",
        value="Alice",
        char_offset_start=0,
        char_offset_end=5,
        grounded=True,
        attempts=1,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        raw.value = "Bob"  # type: ignore[misc]  # mutation attempt is the point of the test


def test_raw_extraction_grounded_without_offsets_raises() -> None:
    with pytest.raises(ValueError, match="grounded=True requires both char_offset_start"):
        RawExtraction(
            field_name="name",
            value="Alice",
            char_offset_start=None,
            char_offset_end=None,
            grounded=True,
            attempts=1,
        )


def test_raw_extraction_grounded_with_one_offset_missing_raises() -> None:
    with pytest.raises(ValueError, match="grounded=True requires both"):
        RawExtraction(
            field_name="name",
            value="Alice",
            char_offset_start=0,
            char_offset_end=None,
            grounded=True,
            attempts=1,
        )


def test_raw_extraction_ungrounded_with_non_none_offsets_raises() -> None:
    with pytest.raises(ValueError, match="grounded=False requires both"):
        RawExtraction(
            field_name="name",
            value="Alice",
            char_offset_start=0,
            char_offset_end=5,
            grounded=False,
            attempts=1,
        )


def test_raw_extraction_grounded_with_negative_offset_raises() -> None:
    with pytest.raises(ValueError, match="non-negative offsets"):
        RawExtraction(
            field_name="name",
            value="Alice",
            char_offset_start=-1,
            char_offset_end=5,
            grounded=True,
            attempts=1,
        )


def test_raw_extraction_grounded_with_equal_offsets_raises() -> None:
    with pytest.raises(ValueError, match="char_offset_start < char_offset_end"):
        RawExtraction(
            field_name="name",
            value="Alice",
            char_offset_start=5,
            char_offset_end=5,
            grounded=True,
            attempts=1,
        )


def test_raw_extraction_grounded_with_start_greater_than_end_raises() -> None:
    with pytest.raises(ValueError, match="char_offset_start < char_offset_end"):
        RawExtraction(
            field_name="name",
            value="Alice",
            char_offset_start=10,
            char_offset_end=5,
            grounded=True,
            attempts=1,
        )


def test_raw_extraction_ungrounded_with_none_offsets_is_valid() -> None:
    raw = RawExtraction(
        field_name="nationality",
        value="French",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )

    assert raw.grounded is False
    assert raw.char_offset_start is None
    assert raw.char_offset_end is None

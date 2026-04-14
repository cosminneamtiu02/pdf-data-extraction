"""Unit tests for BoundingBoxRef."""

import pytest
from pydantic import ValidationError

from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef


def test_bounding_box_ref_valid_construction_exposes_fields() -> None:
    bbox = BoundingBoxRef(page=1, x0=0.0, y0=0.0, x1=100.0, y1=20.0)

    assert bbox.page == 1
    assert bbox.x0 == 0.0
    assert bbox.y0 == 0.0
    assert bbox.x1 == 100.0
    assert bbox.y1 == 20.0


def test_bounding_box_ref_serializes_with_exactly_five_keys() -> None:
    bbox = BoundingBoxRef(page=3, x0=1.5, y0=2.5, x1=10.5, y1=20.5)

    dumped = bbox.model_dump()

    assert set(dumped.keys()) == {"page", "x0", "y0", "x1", "y1"}
    assert dumped == {"page": 3, "x0": 1.5, "y0": 2.5, "x1": 10.5, "y1": 20.5}


def test_bounding_box_ref_round_trip_through_json() -> None:
    original = BoundingBoxRef(page=2, x0=0.0, y0=0.0, x1=100.0, y1=20.0)

    parsed = BoundingBoxRef.model_validate_json(original.model_dump_json())

    assert parsed == original


def test_bounding_box_ref_page_zero_rejected() -> None:
    with pytest.raises(ValidationError, match="page"):
        BoundingBoxRef(page=0, x0=0.0, y0=0.0, x1=1.0, y1=1.0)


def test_bounding_box_ref_page_negative_rejected() -> None:
    with pytest.raises(ValidationError, match="page"):
        BoundingBoxRef(page=-5, x0=0.0, y0=0.0, x1=1.0, y1=1.0)


def test_bounding_box_ref_inverted_x_rejected() -> None:
    with pytest.raises(ValidationError, match="x0"):
        BoundingBoxRef(page=1, x0=10.0, y0=0.0, x1=5.0, y1=20.0)


def test_bounding_box_ref_inverted_y_rejected() -> None:
    with pytest.raises(ValidationError, match="y0"):
        BoundingBoxRef(page=1, x0=0.0, y0=20.0, x1=10.0, y1=5.0)


def test_bounding_box_ref_zero_area_allowed() -> None:
    bbox = BoundingBoxRef(page=1, x0=5.0, y0=5.0, x1=5.0, y1=5.0)

    assert bbox.x0 == bbox.x1 == 5.0
    assert bbox.y0 == bbox.y1 == 5.0

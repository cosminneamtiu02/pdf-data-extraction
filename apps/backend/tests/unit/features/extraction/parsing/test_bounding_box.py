"""Unit tests for BoundingBox dataclass."""

import dataclasses

import pytest

from app.features.extraction.parsing.bounding_box import BoundingBox


def test_bounding_box_valid_construction_exposes_fields() -> None:
    bbox = BoundingBox(x0=0.0, y0=0.0, x1=100.0, y1=20.0)

    assert bbox.x0 == 0.0
    assert bbox.y0 == 0.0
    assert bbox.x1 == 100.0
    assert bbox.y1 == 20.0


def test_bounding_box_inverted_x_raises() -> None:
    with pytest.raises(ValueError, match="x0"):
        BoundingBox(x0=10.0, y0=20.0, x1=5.0, y1=30.0)


def test_bounding_box_inverted_y_raises() -> None:
    with pytest.raises(ValueError, match="y0"):
        BoundingBox(x0=0.0, y0=30.0, x1=5.0, y1=10.0)


def test_bounding_box_degenerate_equal_edges_allowed() -> None:
    bbox = BoundingBox(x0=5.0, y0=5.0, x1=5.0, y1=5.0)

    assert bbox.x0 == bbox.x1
    assert bbox.y0 == bbox.y1


def test_bounding_box_is_frozen() -> None:
    bbox = BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)

    with pytest.raises(dataclasses.FrozenInstanceError):
        bbox.x0 = 99.0  # type: ignore[misc]

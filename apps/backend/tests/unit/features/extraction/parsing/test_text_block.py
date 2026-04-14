"""Unit tests for TextBlock dataclass."""

import dataclasses

import pytest

from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.text_block import TextBlock


def _bbox() -> BoundingBox:
    return BoundingBox(x0=0.0, y0=0.0, x1=10.0, y1=10.0)


def test_text_block_valid_construction_exposes_fields() -> None:
    block = TextBlock(text="hello", page_number=1, bbox=_bbox(), block_id="p1_b0")

    assert block.text == "hello"
    assert block.page_number == 1
    assert block.bbox == _bbox()
    assert block.block_id == "p1_b0"


def test_text_block_page_number_zero_raises() -> None:
    with pytest.raises(ValueError, match=r"page_number.*1-indexed"):
        TextBlock(text="hello", page_number=0, bbox=_bbox(), block_id="p1_b0")


def test_text_block_page_number_negative_raises() -> None:
    with pytest.raises(ValueError, match=r"page_number.*1-indexed"):
        TextBlock(text="hello", page_number=-1, bbox=_bbox(), block_id="p1_b0")


def test_text_block_page_number_two_accepted() -> None:
    block = TextBlock(text="hello", page_number=2, bbox=_bbox(), block_id="p2_b0")

    assert block.page_number == 2


def test_text_block_is_frozen() -> None:
    block = TextBlock(text="hello", page_number=1, bbox=_bbox(), block_id="p1_b0")

    with pytest.raises(dataclasses.FrozenInstanceError):
        block.text = "world"  # type: ignore[misc]

"""Unit tests for the CharRange immutable dataclass."""

import dataclasses

import pytest

from app.features.extraction.coordinates.char_range import CharRange


def test_char_range_valid_construction_exposes_fields() -> None:
    rng = CharRange(start=3, end=7)

    assert rng.start == 3
    assert rng.end == 7


def test_char_range_degenerate_start_equals_end_allowed() -> None:
    rng = CharRange(start=0, end=0)

    assert rng.start == rng.end == 0


def test_char_range_inverted_start_greater_than_end_raises() -> None:
    with pytest.raises(ValueError, match="start"):
        CharRange(start=5, end=3)


def test_char_range_equality_structural() -> None:
    assert CharRange(3, 7) == CharRange(3, 7)
    assert CharRange(3, 7) != CharRange(3, 8)


def test_char_range_is_frozen() -> None:
    rng = CharRange(start=0, end=1)

    with pytest.raises(dataclasses.FrozenInstanceError):
        # type: ignore[misc] — intentional: exercises the runtime frozen-dataclass guard.
        rng.start = 99  # type: ignore[misc]

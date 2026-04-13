"""Tests for the Money value object."""

import pytest

from app.types.money import Money


def test_money_creation_with_valid_currency():
    """Money should construct with valid ISO 4217 currency."""
    m = Money(amount_minor=1050, currency="USD")
    assert m.amount_minor == 1050
    assert m.currency == "USD"


def test_money_rejects_invalid_currency_code():
    """Money should reject non-ISO-4217 currency codes."""
    with pytest.raises(ValueError, match="currency"):
        Money(amount_minor=100, currency="XYZ")


def test_money_amount_is_integer_minor_units():
    """Money amount must be an integer (minor units, e.g. cents)."""
    m = Money(amount_minor=999, currency="EUR")
    assert isinstance(m.amount_minor, int)


def test_money_rejects_float_amount():
    """Money should reject float amounts."""
    with pytest.raises((TypeError, ValueError)):
        Money(amount_minor=10.5, currency="USD")  # type: ignore[arg-type]

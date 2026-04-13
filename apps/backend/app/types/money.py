"""Money value object — integer minor units + ISO 4217 currency."""

from typing import Any, cast

from pydantic import BaseModel, field_validator, model_validator

from app.types.currency import validate_currency


class Money(BaseModel, frozen=True):
    """Immutable money value object.

    amount_minor is in the smallest currency unit (e.g. cents for USD).
    currency is a 3-letter ISO 4217 code.
    """

    amount_minor: int
    currency: str

    @field_validator("currency")
    @classmethod
    def validate_currency_code(cls, v: str) -> str:
        return validate_currency(v)

    @model_validator(mode="before")
    @classmethod
    def reject_float_amount(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw = cast("dict[str, Any]", data)
            if isinstance(raw.get("amount_minor"), float):
                msg = "amount_minor must be an integer (minor units), not a float"
                raise TypeError(msg)
            return raw
        return data

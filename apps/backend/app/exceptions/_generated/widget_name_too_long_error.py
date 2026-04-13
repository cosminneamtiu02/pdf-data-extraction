"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.widget_name_too_long_params import WidgetNameTooLongParams
from app.exceptions.base import DomainError


class WidgetNameTooLongError(DomainError):
    """Error: WIDGET_NAME_TOO_LONG."""

    code: ClassVar[str] = "WIDGET_NAME_TOO_LONG"
    http_status: ClassVar[int] = 422

    def __init__(self, *, name: str, max_length: int, actual_length: int) -> None:
        super().__init__(
            params=WidgetNameTooLongParams(
                name=name,
                max_length=max_length,
                actual_length=actual_length,
            ),
        )

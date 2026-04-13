"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.widget_not_found_params import WidgetNotFoundParams
from app.exceptions.base import DomainError


class WidgetNotFoundError(DomainError):
    """Error: WIDGET_NOT_FOUND."""

    code: ClassVar[str] = "WIDGET_NOT_FOUND"
    http_status: ClassVar[int] = 404

    def __init__(self, *, widget_id: str) -> None:
        super().__init__(params=WidgetNotFoundParams(widget_id=widget_id))

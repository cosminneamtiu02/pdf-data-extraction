"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.widget_name_conflict_params import WidgetNameConflictParams
from app.exceptions.base import DomainError


class WidgetNameConflictError(DomainError):
    """Error: WIDGET_NAME_CONFLICT."""

    code: ClassVar[str] = "WIDGET_NAME_CONFLICT"
    http_status: ClassVar[int] = 409

    def __init__(self, *, name: str) -> None:
        super().__init__(params=WidgetNameConflictParams(name=name))

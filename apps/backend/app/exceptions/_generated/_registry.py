"""Generated error registry. Do not edit."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.exceptions.base import DomainError

from app.exceptions._generated.conflict_error import ConflictError
from app.exceptions._generated.internal_error import InternalError
from app.exceptions._generated.not_found_error import NotFoundError
from app.exceptions._generated.rate_limited_error import RateLimitedError
from app.exceptions._generated.validation_failed_error import ValidationFailedError
from app.exceptions._generated.widget_name_conflict_error import WidgetNameConflictError
from app.exceptions._generated.widget_name_too_long_error import WidgetNameTooLongError
from app.exceptions._generated.widget_not_found_error import WidgetNotFoundError

ERROR_CLASSES: dict[str, type[DomainError]] = {
    "NOT_FOUND": NotFoundError,
    "CONFLICT": ConflictError,
    "VALIDATION_FAILED": ValidationFailedError,
    "INTERNAL_ERROR": InternalError,
    "RATE_LIMITED": RateLimitedError,
    "WIDGET_NOT_FOUND": WidgetNotFoundError,
    "WIDGET_NAME_CONFLICT": WidgetNameConflictError,
    "WIDGET_NAME_TOO_LONG": WidgetNameTooLongError,
}

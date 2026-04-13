"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.validation_failed_params import ValidationFailedParams
from app.exceptions.base import DomainError


class ValidationFailedError(DomainError):
    """Error: VALIDATION_FAILED."""

    code: ClassVar[str] = "VALIDATION_FAILED"
    http_status: ClassVar[int] = 422

    def __init__(self, *, field: str, reason: str) -> None:
        super().__init__(params=ValidationFailedParams(field=field, reason=reason))

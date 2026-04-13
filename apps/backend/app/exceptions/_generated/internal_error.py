"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions.base import DomainError


class InternalError(DomainError):
    """Error: INTERNAL_ERROR."""

    code: ClassVar[str] = "INTERNAL_ERROR"
    http_status: ClassVar[int] = 500

    def __init__(self) -> None:
        super().__init__(params=None)

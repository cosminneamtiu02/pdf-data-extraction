"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions.base import DomainError


class ConflictError(DomainError):
    """Error: CONFLICT."""

    code: ClassVar[str] = "CONFLICT"
    http_status: ClassVar[int] = 409

    def __init__(self) -> None:
        super().__init__(params=None)

"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions.base import DomainError


class StructuredOutputFailedError(DomainError):
    """Error: STRUCTURED_OUTPUT_FAILED."""

    code: ClassVar[str] = "STRUCTURED_OUTPUT_FAILED"
    http_status: ClassVar[int] = 502

    def __init__(self) -> None:
        super().__init__(params=None)

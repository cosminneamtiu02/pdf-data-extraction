"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions.base import DomainError


class PdfInvalidError(DomainError):
    """Error: PDF_INVALID."""

    code: ClassVar[str] = "PDF_INVALID"
    http_status: ClassVar[int] = 400

    def __init__(self) -> None:
        super().__init__(params=None)

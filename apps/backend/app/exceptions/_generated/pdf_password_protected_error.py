"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions.base import DomainError


class PdfPasswordProtectedError(DomainError):
    """Error: PDF_PASSWORD_PROTECTED."""

    code: ClassVar[str] = "PDF_PASSWORD_PROTECTED"
    http_status: ClassVar[int] = 400

    def __init__(self) -> None:
        super().__init__(params=None)

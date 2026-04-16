"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.pdf_too_large_params import PdfTooLargeParams
from app.exceptions.base import DomainError


class PdfTooLargeError(DomainError):
    """Error: PDF_TOO_LARGE."""

    code: ClassVar[str] = "PDF_TOO_LARGE"
    http_status: ClassVar[int] = 413

    def __init__(self, *, max_bytes: int, actual_bytes: int) -> None:
        super().__init__(params=PdfTooLargeParams(max_bytes=max_bytes, actual_bytes=actual_bytes))

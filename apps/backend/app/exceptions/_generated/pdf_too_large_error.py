"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from typing import ClassVar

from app.exceptions._generated.pdf_too_large_params import PdfTooLargeParams
from app.exceptions.base import DomainError


class PdfTooLargeError(DomainError):
    """Error: PDF_TOO_LARGE."""

    code: ClassVar[str] = "PDF_TOO_LARGE"
    http_status: ClassVar[int] = 413

    def __init__(self, *, actual_bytes: int, max_bytes: int) -> None:
        super().__init__(params=PdfTooLargeParams(actual_bytes=actual_bytes, max_bytes=max_bytes))

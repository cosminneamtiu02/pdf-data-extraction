"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.pdf_too_many_pages_params import PdfTooManyPagesParams
from app.exceptions.base import DomainError


class PdfTooManyPagesError(DomainError):
    """Error: PDF_TOO_MANY_PAGES."""

    code: ClassVar[str] = "PDF_TOO_MANY_PAGES"
    http_status: ClassVar[int] = 413

    def __init__(self, *, actual: int, limit: int) -> None:
        super().__init__(params=PdfTooManyPagesParams(actual=actual, limit=limit))

"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from typing import ClassVar

from app.exceptions.base import DomainError


class PdfNoTextExtractableError(DomainError):
    """Error: PDF_NO_TEXT_EXTRACTABLE."""

    code: ClassVar[str] = "PDF_NO_TEXT_EXTRACTABLE"
    http_status: ClassVar[int] = 422

    def __init__(self) -> None:
        super().__init__(params=None)

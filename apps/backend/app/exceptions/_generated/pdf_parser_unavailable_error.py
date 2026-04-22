"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from typing import ClassVar

from app.exceptions._generated.pdf_parser_unavailable_params import PdfParserUnavailableParams
from app.exceptions.base import DomainError


class PdfParserUnavailableError(DomainError):
    """Error: PDF_PARSER_UNAVAILABLE."""

    code: ClassVar[str] = "PDF_PARSER_UNAVAILABLE"
    http_status: ClassVar[int] = 500

    def __init__(self, *, dependency: str) -> None:
        super().__init__(params=PdfParserUnavailableParams(dependency=dependency))

"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class PdfParserUnavailableParams(BaseModel):
    """Parameters for PDF_PARSER_UNAVAILABLE error."""

    dependency: str

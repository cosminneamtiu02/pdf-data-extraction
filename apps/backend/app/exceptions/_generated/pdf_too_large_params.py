"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class PdfTooLargeParams(BaseModel):
    """Parameters for PDF_TOO_LARGE error."""

    max_bytes: int
    actual_bytes: int

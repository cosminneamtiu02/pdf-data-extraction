"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class PdfTooLargeParams(BaseModel):
    """Parameters for PDF_TOO_LARGE error."""

    actual_bytes: int
    max_bytes: int

"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class PdfTooManyPagesParams(BaseModel):
    """Parameters for PDF_TOO_MANY_PAGES error."""

    actual: int
    limit: int

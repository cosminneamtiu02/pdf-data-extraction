"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from pydantic import BaseModel


class PdfTooManyPagesParams(BaseModel):
    """Parameters for PDF_TOO_MANY_PAGES error."""

    actual: int
    limit: int

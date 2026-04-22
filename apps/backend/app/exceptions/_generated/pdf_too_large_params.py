"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from pydantic import BaseModel


class PdfTooLargeParams(BaseModel):
    """Parameters for PDF_TOO_LARGE error."""

    actual_bytes: int
    max_bytes: int

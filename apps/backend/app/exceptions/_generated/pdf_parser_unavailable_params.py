"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from pydantic import BaseModel


class PdfParserUnavailableParams(BaseModel):
    """Parameters for PDF_PARSER_UNAVAILABLE error."""

    dependency: str

"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from pydantic import BaseModel


class ExtractionOverloadedParams(BaseModel):
    """Parameters for EXTRACTION_OVERLOADED error."""

    max_concurrent: int

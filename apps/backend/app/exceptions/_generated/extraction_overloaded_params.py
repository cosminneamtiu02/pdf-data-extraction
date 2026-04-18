"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class ExtractionOverloadedParams(BaseModel):
    """Parameters for EXTRACTION_OVERLOADED error."""

    max_concurrent: int

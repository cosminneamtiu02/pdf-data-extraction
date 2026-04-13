"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class ValidationFailedParams(BaseModel):
    """Parameters for VALIDATION_FAILED error."""

    field: str
    reason: str

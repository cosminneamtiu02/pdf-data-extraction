"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class RateLimitedParams(BaseModel):
    """Parameters for RATE_LIMITED error."""

    retry_after_seconds: int

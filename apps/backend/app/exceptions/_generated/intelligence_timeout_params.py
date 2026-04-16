"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class IntelligenceTimeoutParams(BaseModel):
    """Parameters for INTELLIGENCE_TIMEOUT error."""

    budget_seconds: float

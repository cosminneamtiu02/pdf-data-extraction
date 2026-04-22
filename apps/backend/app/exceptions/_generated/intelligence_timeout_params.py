"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from pydantic import BaseModel


class IntelligenceTimeoutParams(BaseModel):
    """Parameters for INTELLIGENCE_TIMEOUT error."""

    budget_seconds: float

"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from pydantic import BaseModel


class ExtractionBudgetExceededParams(BaseModel):
    """Parameters for EXTRACTION_BUDGET_EXCEEDED error."""

    budget_seconds: float

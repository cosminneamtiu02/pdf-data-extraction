"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.extraction_budget_exceeded_params import (
    ExtractionBudgetExceededParams,
)
from app.exceptions.base import DomainError


class ExtractionBudgetExceededError(DomainError):
    """Error: EXTRACTION_BUDGET_EXCEEDED."""

    code: ClassVar[str] = "EXTRACTION_BUDGET_EXCEEDED"
    http_status: ClassVar[int] = 504

    def __init__(self, *, budget_seconds: float) -> None:
        super().__init__(params=ExtractionBudgetExceededParams(budget_seconds=budget_seconds))

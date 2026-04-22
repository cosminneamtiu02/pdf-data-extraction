"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from typing import ClassVar

from app.exceptions._generated.intelligence_timeout_params import IntelligenceTimeoutParams
from app.exceptions.base import DomainError


class IntelligenceTimeoutError(DomainError):
    """Error: INTELLIGENCE_TIMEOUT."""

    code: ClassVar[str] = "INTELLIGENCE_TIMEOUT"
    http_status: ClassVar[int] = 504

    def __init__(self, *, budget_seconds: float) -> None:
        super().__init__(params=IntelligenceTimeoutParams(budget_seconds=budget_seconds))

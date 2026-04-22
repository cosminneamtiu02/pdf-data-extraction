"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from typing import ClassVar

from app.exceptions.base import DomainError


class IntelligenceUnavailableError(DomainError):
    """Error: INTELLIGENCE_UNAVAILABLE."""

    code: ClassVar[str] = "INTELLIGENCE_UNAVAILABLE"
    http_status: ClassVar[int] = 503

    def __init__(self) -> None:
        super().__init__(params=None)

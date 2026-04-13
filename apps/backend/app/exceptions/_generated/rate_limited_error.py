"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.rate_limited_params import RateLimitedParams
from app.exceptions.base import DomainError


class RateLimitedError(DomainError):
    """Error: RATE_LIMITED."""

    code: ClassVar[str] = "RATE_LIMITED"
    http_status: ClassVar[int] = 429

    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__(params=RateLimitedParams(retry_after_seconds=retry_after_seconds))

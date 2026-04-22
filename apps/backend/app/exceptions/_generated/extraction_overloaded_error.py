"""Generated from errors.yaml. Do not edit.

Run ``task errors:generate`` to regenerate after editing errors.yaml.
"""

from typing import ClassVar

from app.exceptions._generated.extraction_overloaded_params import ExtractionOverloadedParams
from app.exceptions.base import DomainError


class ExtractionOverloadedError(DomainError):
    """Error: EXTRACTION_OVERLOADED."""

    code: ClassVar[str] = "EXTRACTION_OVERLOADED"
    http_status: ClassVar[int] = 503

    def __init__(self, *, max_concurrent: int) -> None:
        super().__init__(params=ExtractionOverloadedParams(max_concurrent=max_concurrent))

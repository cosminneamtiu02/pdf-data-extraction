"""Generated from errors.yaml. Do not edit."""

from typing import ClassVar

from app.exceptions._generated.skill_validation_failed_params import SkillValidationFailedParams
from app.exceptions.base import DomainError


class SkillValidationFailedError(DomainError):
    """Error: SKILL_VALIDATION_FAILED."""

    code: ClassVar[str] = "SKILL_VALIDATION_FAILED"
    http_status: ClassVar[int] = 500

    def __init__(self, *, file: str, reason: str) -> None:
        super().__init__(params=SkillValidationFailedParams(file=file, reason=reason))

"""Generated error registry. Do not edit."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.exceptions.base import DomainError

from app.exceptions._generated.conflict_error import ConflictError
from app.exceptions._generated.internal_error import InternalError
from app.exceptions._generated.not_found_error import NotFoundError
from app.exceptions._generated.skill_not_found_error import SkillNotFoundError
from app.exceptions._generated.skill_validation_failed_error import SkillValidationFailedError
from app.exceptions._generated.validation_failed_error import ValidationFailedError

ERROR_CLASSES: dict[str, type[DomainError]] = {
    "NOT_FOUND": NotFoundError,
    "CONFLICT": ConflictError,
    "VALIDATION_FAILED": ValidationFailedError,
    "INTERNAL_ERROR": InternalError,
    "SKILL_VALIDATION_FAILED": SkillValidationFailedError,
    "SKILL_NOT_FOUND": SkillNotFoundError,
}

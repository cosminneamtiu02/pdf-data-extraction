"""Domain exception hierarchy.

Import errors from this module, never from _generated/ directly.
"""

from app.exceptions._generated import (
    InternalError,
    NotFoundError,
    SkillValidationFailedError,
    ValidationFailedError,
)
from app.exceptions.base import DomainError

__all__ = [
    "DomainError",
    "InternalError",
    "NotFoundError",
    "SkillValidationFailedError",
    "ValidationFailedError",
]

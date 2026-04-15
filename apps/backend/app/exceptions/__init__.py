"""Domain exception hierarchy.

Import errors from this module, never from _generated/ directly.
"""

from app.exceptions._generated import (
    IntelligenceUnavailableError,
    InternalError,
    NotFoundError,
    SkillNotFoundError,
    SkillValidationFailedError,
    StructuredOutputFailedError,
    ValidationFailedError,
)
from app.exceptions.base import DomainError

__all__ = [
    "DomainError",
    "IntelligenceUnavailableError",
    "InternalError",
    "NotFoundError",
    "SkillNotFoundError",
    "SkillValidationFailedError",
    "StructuredOutputFailedError",
    "ValidationFailedError",
]

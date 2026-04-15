"""Domain exception hierarchy.

Import errors from this module, never from _generated/ directly.
"""

from app.exceptions._generated import (
    InternalError,
    NotFoundError,
    PdfInvalidError,
    PdfNoTextExtractableError,
    PdfPasswordProtectedError,
    PdfTooManyPagesError,
    SkillNotFoundError,
    SkillValidationFailedError,
    ValidationFailedError,
)
from app.exceptions.base import DomainError

__all__ = [
    "DomainError",
    "InternalError",
    "NotFoundError",
    "PdfInvalidError",
    "PdfNoTextExtractableError",
    "PdfPasswordProtectedError",
    "PdfTooManyPagesError",
    "SkillNotFoundError",
    "SkillValidationFailedError",
    "ValidationFailedError",
]

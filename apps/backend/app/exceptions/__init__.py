"""Domain exception hierarchy.

Import errors from this module, never from _generated/ directly.
"""

from app.exceptions._generated import (
    IntelligenceTimeoutError,
    IntelligenceUnavailableError,
    InternalError,
    NotFoundError,
    PdfInvalidError,
    PdfNoTextExtractableError,
    PdfParserUnavailableError,
    PdfPasswordProtectedError,
    PdfTooLargeError,
    PdfTooManyPagesError,
    SkillNotFoundError,
    SkillValidationFailedError,
    StructuredOutputFailedError,
    ValidationFailedError,
)
from app.exceptions.base import DomainError

__all__ = [
    "DomainError",
    "IntelligenceTimeoutError",
    "IntelligenceUnavailableError",
    "InternalError",
    "NotFoundError",
    "PdfInvalidError",
    "PdfNoTextExtractableError",
    "PdfParserUnavailableError",
    "PdfPasswordProtectedError",
    "PdfTooLargeError",
    "PdfTooManyPagesError",
    "SkillNotFoundError",
    "SkillValidationFailedError",
    "StructuredOutputFailedError",
    "ValidationFailedError",
]

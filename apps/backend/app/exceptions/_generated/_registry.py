"""Generated error registry. Do not edit."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.exceptions.base import DomainError

from app.exceptions._generated.extraction_budget_exceeded_error import ExtractionBudgetExceededError
from app.exceptions._generated.extraction_overloaded_error import ExtractionOverloadedError
from app.exceptions._generated.intelligence_timeout_error import IntelligenceTimeoutError
from app.exceptions._generated.intelligence_unavailable_error import IntelligenceUnavailableError
from app.exceptions._generated.internal_error import InternalError
from app.exceptions._generated.not_found_error import NotFoundError
from app.exceptions._generated.pdf_invalid_error import PdfInvalidError
from app.exceptions._generated.pdf_no_text_extractable_error import PdfNoTextExtractableError
from app.exceptions._generated.pdf_parser_unavailable_error import PdfParserUnavailableError
from app.exceptions._generated.pdf_password_protected_error import PdfPasswordProtectedError
from app.exceptions._generated.pdf_too_large_error import PdfTooLargeError
from app.exceptions._generated.pdf_too_many_pages_error import PdfTooManyPagesError
from app.exceptions._generated.skill_not_found_error import SkillNotFoundError
from app.exceptions._generated.skill_validation_failed_error import SkillValidationFailedError
from app.exceptions._generated.structured_output_failed_error import StructuredOutputFailedError
from app.exceptions._generated.validation_failed_error import ValidationFailedError

ERROR_CLASSES: dict[str, type[DomainError]] = {
    "EXTRACTION_BUDGET_EXCEEDED": ExtractionBudgetExceededError,
    "EXTRACTION_OVERLOADED": ExtractionOverloadedError,
    "INTELLIGENCE_TIMEOUT": IntelligenceTimeoutError,
    "INTELLIGENCE_UNAVAILABLE": IntelligenceUnavailableError,
    "INTERNAL_ERROR": InternalError,
    "NOT_FOUND": NotFoundError,
    "PDF_INVALID": PdfInvalidError,
    "PDF_NO_TEXT_EXTRACTABLE": PdfNoTextExtractableError,
    "PDF_PARSER_UNAVAILABLE": PdfParserUnavailableError,
    "PDF_PASSWORD_PROTECTED": PdfPasswordProtectedError,
    "PDF_TOO_LARGE": PdfTooLargeError,
    "PDF_TOO_MANY_PAGES": PdfTooManyPagesError,
    "SKILL_NOT_FOUND": SkillNotFoundError,
    "SKILL_VALIDATION_FAILED": SkillValidationFailedError,
    "STRUCTURED_OUTPUT_FAILED": StructuredOutputFailedError,
    "VALIDATION_FAILED": ValidationFailedError,
}

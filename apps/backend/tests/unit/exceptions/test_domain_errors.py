"""Tests for generated domain error classes."""

from app.exceptions import SkillNotFoundError, ValidationFailedError


def test_parameterless_domain_error_constructs_with_code_and_status() -> None:
    """``ValidationFailedError`` is parameterless post-#344 (all detail lives in ``details``).

    The direct-raise path — used when application code wants to signal a
    request-validation failure without the FastAPI request-validator —
    therefore constructs without kwargs and reports ``params is None``.
    """
    error = ValidationFailedError()

    assert error.code == "VALIDATION_FAILED"
    assert error.http_status == 422
    assert error.params is None
    assert "VALIDATION_FAILED" in str(error)


def test_parameterised_domain_error_constructs_with_typed_params() -> None:
    """Generated DomainError subclass should construct with correct code, status, and params.

    ``SkillNotFoundError`` still carries typed params (``name`` and
    ``version``) and serialises them via ``model_dump``, so it exercises
    the parameterised-DomainError path that ``ValidationFailedError`` used
    to before #344 dropped its params in favour of multi-field ``details``.
    """
    error = SkillNotFoundError(name="invoice", version="1")

    assert error.code == "SKILL_NOT_FOUND"
    assert error.http_status == 404
    assert error.params is not None
    assert error.params.model_dump() == {"name": "invoice", "version": "1"}
    assert "SKILL_NOT_FOUND" in str(error)

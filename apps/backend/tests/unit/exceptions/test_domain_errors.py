"""Tests for generated domain error classes."""

from app.exceptions import ValidationFailedError


def test_domain_error_constructs_with_typed_params() -> None:
    """Generated DomainError subclass should construct with correct code, status, and params."""
    error = ValidationFailedError(field="name", reason="too short")

    assert error.code == "VALIDATION_FAILED"
    assert error.http_status == 422
    assert error.params is not None
    assert error.params.model_dump() == {"field": "name", "reason": "too short"}
    assert "VALIDATION_FAILED" in str(error)

"""Tests for generated domain error classes."""

from app.exceptions import WidgetNotFoundError


def test_domain_error_constructs_with_typed_params():
    """Generated DomainError subclass should construct with correct code, status, and params."""
    error = WidgetNotFoundError(widget_id="abc-123")

    assert error.code == "WIDGET_NOT_FOUND"
    assert error.http_status == 404
    assert error.params is not None
    assert error.params.model_dump() == {"widget_id": "abc-123"}
    assert "WIDGET_NOT_FOUND" in str(error)

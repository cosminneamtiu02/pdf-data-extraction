"""Tests for the exception handler."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.exceptions import WidgetNotFoundError


def _create_test_app_with_handler() -> FastAPI:
    """Create a minimal FastAPI app with the exception handler registered."""
    from app.api.errors import register_exception_handlers

    test_app = FastAPI()
    register_exception_handlers(test_app)

    @test_app.get("/trigger-domain-error")
    async def trigger_domain_error():
        raise WidgetNotFoundError(widget_id="test-123")

    @test_app.get("/trigger-unhandled")
    async def trigger_unhandled():
        msg = "Something unexpected"
        raise RuntimeError(msg)

    @test_app.get("/trigger-validation")
    async def trigger_validation(required_param: int):  # noqa: ARG001
        return {"ok": True}

    # Add request_id middleware for the handler to read
    from app.api.middleware import RequestIdMiddleware

    test_app.add_middleware(RequestIdMiddleware)

    return test_app


@pytest.fixture
def test_client() -> TestClient:
    """Provide a TestClient with exception handlers registered."""
    return TestClient(_create_test_app_with_handler(), raise_server_exceptions=False)


def test_error_handler_serializes_domain_error(test_client: TestClient):
    """Exception handler should serialize DomainError to {error: {code, params, details, request_id}}."""
    response = test_client.get("/trigger-domain-error")

    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == "WIDGET_NOT_FOUND"
    assert body["error"]["params"] == {"widget_id": "test-123"}
    assert body["error"]["details"] is None
    assert "request_id" in body["error"]


def test_error_handler_maps_validation_error(test_client: TestClient):
    """Pydantic RequestValidationError should map to VALIDATION_FAILED."""
    response = test_client.get("/trigger-validation?required_param=not_an_int")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"


def test_error_handler_includes_all_validation_errors_in_details(test_client: TestClient):
    """VALIDATION_FAILED should include all field errors in details array."""
    response = test_client.get("/trigger-validation?required_param=not_an_int")

    body = response.json()
    assert body["error"]["details"] is not None
    assert len(body["error"]["details"]) > 0
    assert "field" in body["error"]["details"][0]
    assert "reason" in body["error"]["details"][0]


def test_error_handler_maps_unhandled_to_internal_error(test_client: TestClient):
    """Unhandled exceptions should map to INTERNAL_ERROR with 500 status."""
    response = test_client.get("/trigger-unhandled")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["params"] == {}
    assert "request_id" in body["error"]

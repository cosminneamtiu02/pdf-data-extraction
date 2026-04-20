"""Tests for the exception handler."""

import ast
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.errors as errors_module
from app.exceptions import InternalError, NotFoundError, ValidationFailedError


def _create_test_app_with_handler() -> FastAPI:
    """Create a minimal FastAPI app with the exception handler registered."""
    from app.api.errors import register_exception_handlers

    test_app = FastAPI()
    register_exception_handlers(test_app)

    @test_app.get("/trigger-domain-error")
    async def trigger_domain_error() -> None:
        raise NotFoundError

    @test_app.get("/trigger-5xx-domain-error")
    async def trigger_5xx_domain_error() -> None:
        raise InternalError

    @test_app.get("/trigger-validation-error")
    async def trigger_validation_error() -> None:
        raise ValidationFailedError(field="name", reason="too short")

    @test_app.get("/trigger-unhandled")
    async def trigger_unhandled() -> None:
        msg = "Something unexpected"
        raise RuntimeError(msg)

    @test_app.get("/trigger-validation")
    async def trigger_validation(required_param: int) -> dict[str, bool]:  # noqa: ARG001
        return {"ok": True}

    # Add request_id middleware for the handler to read
    from app.api.request_id_middleware import RequestIdMiddleware

    test_app.add_middleware(RequestIdMiddleware)

    return test_app


@pytest.fixture
def test_client() -> TestClient:
    """Provide a TestClient with exception handlers registered."""
    return TestClient(_create_test_app_with_handler(), raise_server_exceptions=False)


def test_error_handler_serializes_domain_error(test_client: TestClient) -> None:
    """Exception handler should serialize DomainError to {error: {code, params, details, request_id}}."""
    response = test_client.get("/trigger-domain-error")

    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["params"] == {}
    assert body["error"]["details"] is None
    assert "request_id" in body["error"]


def test_error_handler_serializes_parameterised_domain_error(test_client: TestClient) -> None:
    """A parameterised DomainError should serialize its params dict."""
    response = test_client.get("/trigger-validation-error")

    assert response.status_code == ValidationFailedError.http_status
    body = response.json()
    assert body["error"]["code"] == ValidationFailedError.code
    assert body["error"]["params"] == {"field": "name", "reason": "too short"}


def test_error_handler_maps_validation_error(test_client: TestClient) -> None:
    """Pydantic RequestValidationError should map to VALIDATION_FAILED."""
    response = test_client.get("/trigger-validation?required_param=not_an_int")

    assert response.status_code == ValidationFailedError.http_status
    body = response.json()
    assert body["error"]["code"] == ValidationFailedError.code


def test_error_handler_includes_all_validation_errors_in_details(test_client: TestClient) -> None:
    """VALIDATION_FAILED should include all field errors in details array."""
    response = test_client.get("/trigger-validation?required_param=not_an_int")

    body = response.json()
    assert body["error"]["details"] is not None
    assert len(body["error"]["details"]) > 0
    assert "field" in body["error"]["details"][0]
    assert "reason" in body["error"]["details"][0]


def test_error_handler_maps_unhandled_to_internal_error(test_client: TestClient) -> None:
    """Unhandled exceptions should map to INTERNAL_ERROR with 500 status."""
    response = test_client.get("/trigger-unhandled")

    assert response.status_code == InternalError.http_status
    body = response.json()
    assert body["error"]["code"] == InternalError.code
    assert body["error"]["params"] == {}
    assert "request_id" in body["error"]


def test_error_handler_validation_code_sourced_from_generated_error(
    test_client: TestClient,
) -> None:
    """The VALIDATION_FAILED body code must equal ``ValidationFailedError.code``.

    This assertion protects against drift if ``errors.yaml`` ever renames the
    code: the handler must source the code from the generated class, not from
    a hardcoded string literal.
    """
    response = test_client.get("/trigger-validation?required_param=not_an_int")

    body = response.json()
    assert body["error"]["code"] == ValidationFailedError.code


def test_error_handler_internal_code_sourced_from_generated_error(test_client: TestClient) -> None:
    """The INTERNAL_ERROR body code must equal ``InternalError.code``.

    Same drift protection as the VALIDATION_FAILED counterpart.
    """
    response = test_client.get("/trigger-unhandled")

    body = response.json()
    assert body["error"]["code"] == InternalError.code


def test_error_handler_validation_status_sourced_from_generated_error(
    test_client: TestClient,
) -> None:
    """The VALIDATION_FAILED response status must equal ``ValidationFailedError.http_status``.

    Same drift-avoidance motivation as the ``.code`` test: ``errors.yaml`` is
    the source of truth for HTTP status as well, and the handler must not
    silently diverge from the contract if the status changes.
    """
    response = test_client.get("/trigger-validation?required_param=not_an_int")

    assert response.status_code == ValidationFailedError.http_status


def test_error_handler_internal_status_sourced_from_generated_error(
    test_client: TestClient,
) -> None:
    """The INTERNAL_ERROR response status must equal ``InternalError.http_status``."""
    response = test_client.get("/trigger-unhandled")

    assert response.status_code == InternalError.http_status


_FORBIDDEN_HANDLER_CODE_LITERALS = frozenset({"VALIDATION_FAILED", "INTERNAL_ERROR"})


def test_error_handler_source_has_no_hardcoded_error_codes() -> None:
    """The handler module must not contain the literal error-code strings as constants.

    ``errors.yaml`` is the source of truth; Python codes are generated from
    it. If the handler hardcodes the string, renaming the code in the YAML
    silently diverges the handler from the rest of the codebase. Source the
    code from the generated class attribute instead (see issue #142).

    Implementation notes: the path is derived from ``app.api.errors.__file__``
    so the test follows the Python import system and survives test-directory
    refactors. The scan uses an AST walk over string constants (mirroring the
    pattern used by ``test_gemma_literal_containment``), which catches both
    single- and double-quoted literals and ignores occurrences in comments,
    while still flagging mentions inside docstrings — the conservative choice,
    since a docstring that names the literal is already a drift risk.
    """
    handler_path = Path(errors_module.__file__)
    tree = ast.parse(handler_path.read_text(encoding="utf-8"))
    offenders = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in _FORBIDDEN_HANDLER_CODE_LITERALS
    ]
    assert not offenders, (
        f"{handler_path.name} must not contain hardcoded error-code string "
        f"constants {sorted(_FORBIDDEN_HANDLER_CODE_LITERALS)}; use the "
        f"generated class attributes (e.g. ValidationFailedError.code) "
        f"instead. Offenders: {offenders}"
    )


def test_handle_domain_error_emits_warning_with_exc_info_for_5xx(
    test_client: TestClient,
) -> None:
    """A 5xx DomainError subclass must emit a structured 'domain_error' warning log.

    Issue #323 guard: before this fix the handler was silent, so
    ``InternalError`` / ``IntelligenceUnavailableError`` / ``TimeoutError``
    / etc. produced only an access-log 5xx line with no code, no params,
    no traceback. Observers had to correlate the request_id against the
    exception's origin by hand.

    Spies directly on ``errors_module.logger`` because
    ``structlog.testing.capture_logs`` depends on structlog's own processor
    chain and is bypassed when earlier tests in the suite reroute structlog
    through stdlib logging.
    """
    with patch.object(errors_module, "logger") as mock_logger:
        response = test_client.get("/trigger-5xx-domain-error")

    assert response.status_code == InternalError.http_status

    mock_logger.warning.assert_called_once()
    args, kwargs = mock_logger.warning.call_args
    assert args == ("domain_error",)
    assert kwargs["code"] == InternalError.code
    assert kwargs["http_status"] == InternalError.http_status
    assert kwargs["exc_info"] is True
    assert "request_id" in kwargs

    mock_logger.info.assert_not_called()


def test_handle_domain_error_emits_info_for_4xx(test_client: TestClient) -> None:
    """A 4xx DomainError subclass emits an info-level 'domain_error' event without traceback.

    4xx errors are user-caused (bad input, missing resource) and are
    high-volume by design. Info level keeps them visible in aggregation
    without paging, and ``exc_info`` is omitted to avoid noise in logs.
    """
    with patch.object(errors_module, "logger") as mock_logger:
        response = test_client.get("/trigger-domain-error")

    assert response.status_code == 404  # NotFoundError

    mock_logger.info.assert_called_once()
    args, kwargs = mock_logger.info.call_args
    assert args == ("domain_error",)
    assert kwargs["code"] == NotFoundError.code
    assert kwargs["http_status"] == NotFoundError.http_status
    assert "request_id" in kwargs
    assert "exc_info" not in kwargs

    mock_logger.warning.assert_not_called()

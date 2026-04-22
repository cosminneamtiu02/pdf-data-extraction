"""Tests for the exception handler."""

import ast
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.errors as errors_module
from app.exceptions import (
    DomainError,
    ExtractionOverloadedError,
    IntelligenceTimeoutError,
    IntelligenceUnavailableError,
    InternalError,
    NotFoundError,
    PdfParserUnavailableError,
    StructuredOutputFailedError,
    ValidationFailedError,
)


def _create_test_app_with_handler() -> FastAPI:
    """Create a minimal FastAPI app with the exception handler registered."""
    from fastapi.exceptions import RequestValidationError

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
        raise ValidationFailedError

    @test_app.get("/trigger-unhandled")
    async def trigger_unhandled() -> None:
        msg = "Something unexpected"
        raise RuntimeError(msg)

    @test_app.get("/trigger-validation")
    async def trigger_validation(required_param: int) -> dict[str, bool]:  # noqa: ARG001
        return {"ok": True}

    @test_app.get("/trigger-multi-validation")
    async def trigger_multi_validation(
        first_required: int,  # noqa: ARG001 — required param drives the validation failure
        second_required: int,  # noqa: ARG001 — required param drives the validation failure
    ) -> dict[str, bool]:
        return {"ok": True}

    @test_app.get("/trigger-empty-validation-error")
    async def trigger_empty_validation_error() -> None:
        # Simulates a framework-level anomaly (issue #369): a
        # ``RequestValidationError`` with an empty errors list. FastAPI
        # should never raise this in practice; when it does, the
        # ``unknown/unknown`` fallback that used to live in
        # ``handle_validation_error`` silently covered up the bug. The
        # handler must now surface it via ``InternalError`` so the
        # 5xx ``handle_domain_error`` path logs a traceback.
        raise RequestValidationError(errors=[])

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


def test_error_handler_serializes_validation_failed_domain_error(test_client: TestClient) -> None:
    """A directly-raised ``ValidationFailedError`` serializes as the parameterless envelope.

    After issue #344, VALIDATION_FAILED carries no ``params`` — all
    per-field detail lives in ``details``. A bare ``ValidationFailedError``
    (raised by application code, not FastAPI's request-validation path)
    therefore produces ``params == {}`` and ``details is None``.
    """
    response = test_client.get("/trigger-validation-error")

    assert response.status_code == ValidationFailedError.http_status
    body = response.json()
    assert body["error"]["code"] == ValidationFailedError.code
    assert body["error"]["params"] == {}
    assert body["error"]["details"] is None


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


def test_error_handler_validation_params_is_empty(test_client: TestClient) -> None:
    """VALIDATION_FAILED must expose no ``params`` — per-field info lives in ``details``.

    Issue #344 fix: the prior contract set ``params`` to the first detail
    only, silently truncating multi-field failures. ``params`` is now
    empty for VALIDATION_FAILED; operators consume ``details`` for the
    full list.
    """
    response = test_client.get("/trigger-validation?required_param=not_an_int")

    body = response.json()
    assert body["error"]["params"] == {}


def test_error_handler_surfaces_all_fields_when_multiple_fail(
    test_client: TestClient,
) -> None:
    """When several fields fail validation, ``details`` must carry every one.

    This pins the issue #344 fix: the prior handler collapsed multi-field
    failures into a single-entry ``params`` dict, so an operator reading
    the response saw only one of N violations. The replacement contract
    puts all field failures in ``details`` (a list) and leaves ``params``
    empty. Missing ``first_required`` AND ``second_required`` must yield
    two distinct ``details`` entries with distinct ``field`` values.
    """
    response = test_client.get("/trigger-multi-validation")

    assert response.status_code == ValidationFailedError.http_status
    body = response.json()
    assert body["error"]["code"] == ValidationFailedError.code
    assert body["error"]["params"] == {}

    details = body["error"]["details"]
    assert details is not None
    # Both required query params are missing — expect at least two entries
    # with distinct field identifiers.
    min_multi_field_failures = 2
    assert len(details) >= min_multi_field_failures
    fields = {entry["field"] for entry in details}
    assert any("first_required" in f for f in fields)
    assert any("second_required" in f for f in fields)


def test_handle_validation_error_empty_errors_raises_internal_error(
    test_client: TestClient,
) -> None:
    """An empty ``exc.errors()`` list must surface as INTERNAL_ERROR (issue #369).

    A ``RequestValidationError`` with no underlying Pydantic errors is a
    framework-level anomaly — FastAPI should never raise it in practice.
    The prior ``{'field': 'unknown', 'reason': 'unknown'}`` fallback (and
    its post-#344 replacement of silently returning ``details=[]``)
    papered over the bug by returning a normal 422. The handler must
    instead raise ``InternalError`` so ``handle_domain_error`` catches
    it, logs at warning with ``exc_info=True``, and serves a 500.
    """
    response = test_client.get("/trigger-empty-validation-error")

    assert response.status_code == InternalError.http_status
    body = response.json()
    assert body["error"]["code"] == InternalError.code
    assert body["error"]["params"] == {}
    assert "request_id" in body["error"]


def test_handle_validation_error_empty_errors_logs_traceback(
    test_client: TestClient,
) -> None:
    """The empty-errors anomaly must be logged at warning level with a traceback.

    Pins the observability half of the issue #369 fix: raising
    ``InternalError`` instead of returning a silent 422 is only half the
    point — the other half is that the 5xx ``handle_domain_error`` path
    must fire with ``exc_info=True`` so the framework-level anomaly is
    actionable rather than silently masked.
    """
    with patch.object(errors_module, "_logger") as mock_logger:
        response = test_client.get("/trigger-empty-validation-error")

    assert response.status_code == InternalError.http_status
    mock_logger.warning.assert_called_once()
    args, kwargs = mock_logger.warning.call_args
    assert args == ("domain_error",)
    assert kwargs["code"] == InternalError.code
    assert kwargs["http_status"] == InternalError.http_status
    assert kwargs["exc_info"] is True


async def test_handle_validation_error_empty_errors_chains_original_cause() -> None:
    """The ``InternalError`` raised for empty ``exc.errors()`` must chain the original.

    Copilot review on PR #489 (issue #369 follow-up): a bare
    ``raise InternalError`` sets only ``__context__`` (implicit chaining)
    and leaves ``__cause__`` as ``None``. Aggregators and debuggers that
    walk ``__cause__`` to reconstruct the "caused by" line therefore lose
    the original ``RequestValidationError`` entirely, even though
    ``_logger.warning(..., exc_info=True)`` in ``handle_domain_error``
    prints an ``exc_info`` block -- because ``exc_info`` captures the
    ``InternalError`` currently being raised, not the original. The fix
    is ``raise InternalError from exc`` so ``__cause__`` carries the
    original exception.

    This test invokes the registered handler directly (bypassing the
    TestClient round-trip) so the raised ``InternalError`` can be caught
    and inspected -- a ``TestClient`` exercise would see only the final
    500 response, not the exception object.
    """
    from fastapi.exceptions import RequestValidationError

    from app.api.errors import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)
    handler = app.exception_handlers[RequestValidationError]

    original = RequestValidationError(errors=[])
    # The handler calls ``_get_request_id(request)`` before reaching the
    # empty-errors branch, and that helper reads ``request.state``. A bare
    # ``object()`` has no ``.state`` attribute, so use a ``SimpleNamespace``
    # with an empty ``state`` to mimic the ASGI ``Request`` surface the
    # helper touches -- avoids constructing a full ASGI scope.
    from types import SimpleNamespace

    fake_request = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(InternalError) as exc_info:
        await handler(fake_request, original)  # pyright: ignore[reportGeneralTypeIssues]

    assert exc_info.value.__cause__ is original, (
        "Expected ``raise InternalError from exc`` to set ``__cause__`` to the "
        "original RequestValidationError; got "
        f"{exc_info.value.__cause__!r}. Without explicit chaining, the "
        "warning-level traceback logged by handle_domain_error loses the "
        "original validation exception as the causal link."
    )


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
    ``InternalError`` / ``IntelligenceUnavailableError`` /
    ``IntelligenceTimeoutError`` / etc. produced only an access-log 5xx
    line with no code, no params, no traceback. Observers had to
    correlate the request_id against the exception's origin by hand.

    Spies directly on ``errors_module._logger`` because
    ``structlog.testing.capture_logs`` depends on structlog's own processor
    chain and is bypassed when earlier tests in the suite reroute structlog
    through stdlib logging.
    """
    with patch.object(errors_module, "_logger") as mock_logger:
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
    with patch.object(errors_module, "_logger") as mock_logger:
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


# Issue #317 regression guard: the handler must emit a warning with
# ``exc_info=True`` for every 5xx ``DomainError`` subclass listed in the
# original report (``StructuredOutputFailedError``,
# ``IntelligenceUnavailableError``, ``IntelligenceTimeoutError``,
# ``PdfParserUnavailableError``, ``ExtractionOverloadedError``). These are
# the concrete pipeline errors an on-call responder needs to see with
# code+http_status+traceback; a 5xx that degrades to a silent access-log
# line is the exact footgun #317 flagged. The in-flight test above covers
# the generic ``InternalError``; this one pins the specific named classes
# so a future refactor that special-cases any of them cannot regress the
# contract unnoticed. Class-construction is deferred to a factory because
# several of these take required kwargs (``budget_seconds``, etc.).
_NAMED_5XX_DOMAIN_ERROR_FACTORIES: list[
    tuple[str, type[DomainError], Callable[[], DomainError]]
] = [
    # Parameterless errors: the class itself is a zero-arg callable, so no
    # lambda wrapper is needed (ruff PLW0108).
    (
        "StructuredOutputFailedError",
        StructuredOutputFailedError,
        StructuredOutputFailedError,
    ),
    (
        "IntelligenceUnavailableError",
        IntelligenceUnavailableError,
        IntelligenceUnavailableError,
    ),
    # Parameterised errors: wrap kwargs construction in a lambda.
    (
        "IntelligenceTimeoutError",
        IntelligenceTimeoutError,
        lambda: IntelligenceTimeoutError(budget_seconds=30.0),
    ),
    (
        "PdfParserUnavailableError",
        PdfParserUnavailableError,
        lambda: PdfParserUnavailableError(dependency="docling"),
    ),
    (
        "ExtractionOverloadedError",
        ExtractionOverloadedError,
        lambda: ExtractionOverloadedError(max_concurrent=4),
    ),
]


@pytest.mark.parametrize(
    ("name", "error_cls", "factory"),
    _NAMED_5XX_DOMAIN_ERROR_FACTORIES,
    ids=[name for name, _cls, _factory in _NAMED_5XX_DOMAIN_ERROR_FACTORIES],
)
def test_handle_domain_error_logs_warning_for_each_named_5xx_subclass(
    name: str,
    error_cls: type[DomainError],
    factory: Callable[[], DomainError],
) -> None:
    """Each 5xx DomainError subclass called out in issue #317 must emit the warning log.

    Registers a fresh app so each subclass is actually raised through the
    FastAPI exception handler path (not just constructed and asserted on).
    This catches a failure mode the ``InternalError``-only test cannot:
    a handler that special-cases particular subclasses (e.g. a hypothetical
    future early-return for a specific ``.code``) would silently strip the
    observability event, and only a parametrized pass catches it.
    """
    from app.api.errors import register_exception_handlers
    from app.api.request_id_middleware import RequestIdMiddleware

    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/boom")
    async def boom() -> None:
        raise factory()

    client = TestClient(app, raise_server_exceptions=False)

    with patch.object(errors_module, "_logger") as mock_logger:
        response = client.get("/boom")

    assert response.status_code == error_cls.http_status, (
        f"{name} should surface http_status={error_cls.http_status}"
    )
    assert error_cls.http_status >= 500, f"{name} must be in the 5xx range for this test"

    mock_logger.warning.assert_called_once()
    args, kwargs = mock_logger.warning.call_args
    assert args == ("domain_error",), f"{name} must emit the 'domain_error' warning event"
    assert kwargs["code"] == error_cls.code
    assert kwargs["http_status"] == error_cls.http_status
    assert kwargs["exc_info"] is True, (
        f"{name} must include exc_info=True so the traceback reaches the aggregator"
    )
    assert "request_id" in kwargs
    mock_logger.info.assert_not_called()

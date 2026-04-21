"""Exception handlers — maps DomainError subclasses to HTTP error responses."""

from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.exceptions import InternalError, ValidationFailedError
from app.exceptions.base import DomainError
from app.schemas import ErrorBody, ErrorResponse

logger = structlog.get_logger(__name__)


def _get_request_id(request: Request) -> str:
    """Extract request ID from request state, set by RequestIdMiddleware.

    Falls back to a fresh ``uuid4().hex`` when the middleware is absent so the
    ``X-Request-Id`` header always contains a valid 32-char hex string.
    """
    rid: str | None = getattr(request.state, "request_id", None)
    return rid or uuid4().hex


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app."""

    @app.exception_handler(DomainError)
    async def handle_domain_error(  # pyright: ignore[reportUnusedFunction]  # registered via decorator
        request: Request,
        exc: DomainError,
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        # Observability contract (issue #323): 5xx DomainError subclasses —
        # ``StructuredOutputFailedError``, ``IntelligenceUnavailableError``,
        # ``IntelligenceTimeoutError``, ``PdfParserUnavailableError``,
        # ``ExtractionOverloadedError`` — are on-call-actionable, so emit at
        # warning with ``exc_info=True`` so tracebacks reach the aggregator.
        # 4xx is user-caused and high-volume; info-level keeps it visible
        # without paging and omits the traceback.
        http_5xx_floor = 500
        if exc.http_status >= http_5xx_floor:
            logger.warning(
                "domain_error",
                code=exc.code,
                http_status=exc.http_status,
                request_id=request_id,
                exc_info=True,  # noqa: LOG014 — this function IS a FastAPI exception handler
            )
        else:
            logger.info(
                "domain_error",
                code=exc.code,
                http_status=exc.http_status,
                request_id=request_id,
            )
        # Route through ``ErrorResponse`` / ``ErrorBody`` so Pydantic asserts
        # at response time that the constructed envelope actually matches the
        # declared schema advertised in OpenAPI (issue #345). Building the
        # dict inline bypassed that round-trip and let a future ``*Params``
        # model with ``None`` / nested-object / list values ship silently.
        envelope = ErrorResponse(
            error=ErrorBody(
                code=exc.code,
                params=exc.params.model_dump() if exc.params else {},
                details=None,
                request_id=request_id,
            ),
        )
        return JSONResponse(
            status_code=exc.http_status,
            headers={"X-Request-Id": request_id},
            content=envelope.model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        errors = exc.errors()
        details = [
            {
                "field": " -> ".join(str(loc) for loc in e.get("loc", [])),
                "reason": e.get("msg", "Unknown validation error"),
            }
            for e in errors
        ]
        first = details[0] if details else {"field": "unknown", "reason": "unknown"}
        return JSONResponse(
            status_code=ValidationFailedError.http_status,
            headers={"X-Request-Id": request_id},
            content={
                "error": {
                    "code": ValidationFailedError.code,
                    "params": first,
                    "details": details,
                    "request_id": request_id,
                },
            },
        )

    @app.exception_handler(Exception)
    async def handle_unhandled(  # pyright: ignore[reportUnusedFunction]
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.exception(
            "unhandled_exception",
            request_id=request_id,
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=InternalError.http_status,
            headers={"X-Request-Id": request_id},
            content={
                "error": {
                    "code": InternalError.code,
                    "params": {},
                    "details": None,
                    "request_id": request_id,
                },
            },
        )

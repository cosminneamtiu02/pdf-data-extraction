"""Exception handlers — maps DomainError subclasses to HTTP error responses."""

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.exceptions.base import DomainError

logger = structlog.get_logger(__name__)


def _get_request_id(request: Request) -> str:
    """Extract request ID from request state, set by RequestIdMiddleware."""
    return getattr(request.state, "request_id", "unknown")


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app."""

    @app.exception_handler(DomainError)
    async def handle_domain_error(  # pyright: ignore[reportUnusedFunction]  # registered via decorator
        request: Request,
        exc: DomainError,
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "code": exc.code,
                    "params": exc.params.model_dump() if exc.params else {},
                    "details": None,
                    "request_id": request_id,
                },
            },
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
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_FAILED",
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
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "params": {},
                    "details": None,
                    "request_id": request_id,
                },
            },
        )

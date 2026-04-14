"""Request middleware — request ID, access logging, CORS."""

import re
import time
import uuid

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = structlog.get_logger(__name__)

# Valid 32-char lowercase hex pattern for X-Request-Id (PDFX-E007-F003).
_REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a 32-char hex request id to every request and response.

    Format mandated by PDFX-E007-F003: `uuid.uuid4().hex` (32 lowercase hex chars,
    no dashes). Honors a client-supplied `X-Request-Id` header only when it
    already matches the format, otherwise generates a fresh id.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_id = request.headers.get("X-Request-Id", "")
        request_id = client_id if _REQUEST_ID_PATTERN.match(client_id) else uuid.uuid4().hex
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

        response.headers["X-Request-Id"] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response


def configure_middleware(app: FastAPI, cors_origins: list[str]) -> None:
    """Attach all middleware to the FastAPI app.

    Order matters — outermost middleware runs first. The stack from outside in:
    1. CORS (handles preflight before anything else)
    2. RequestId (sets request_id for all downstream middleware and handlers)
    3. AccessLog (logs after response is generated, includes request_id from contextvars)
    """
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

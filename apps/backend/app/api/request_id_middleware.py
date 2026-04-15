"""RequestIdMiddleware — attach a 32-char hex request id to every request."""

import re
import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

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
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

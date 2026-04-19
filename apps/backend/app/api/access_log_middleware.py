"""AccessLogMiddleware — structured per-request access log."""

import time

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.exceptions.base import DomainError

_logger = structlog.get_logger(__name__)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException as exc:
            # Catch BaseException (not just Exception) so that asyncio.CancelledError
            # from client disconnects still produces an access log entry. Operators
            # diagnosing a disconnect storm otherwise have no per-request record of
            # the failed requests. We re-raise unchanged so the exception handler
            # middleware (or the ASGI server) can do its normal job (issue #147).
            # If the raised exception is a DomainError, record its declared
            # ``http_status`` so the access log matches the response the
            # exception handler will actually emit (e.g., 404 for a
            # NotFoundError) — protects SRE dashboards filtering on
            # ``status_code >= 500`` from mislabeling 4xx domain errors as
            # server errors (issue #238).
            duration_ms = (time.perf_counter() - start) * 1000
            status_code = exc.http_status if isinstance(exc, DomainError) else 500
            _logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
                error=type(exc).__name__,
            )
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        _logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response

"""Unit tests for AccessLogMiddleware."""

import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest
from starlette.types import Receive, Scope, Send
from structlog.testing import capture_logs

from app.api.access_log_middleware import AccessLogMiddleware
from app.exceptions import SkillNotFoundError


class _SentinelError(Exception):
    """Sentinel exception used only by the exception-propagation test."""


async def test_access_log_middleware_emits_log_on_unhandled_exception() -> None:
    """When call_next raises, the middleware must still emit an http_request
    log entry with status_code=500 and a positive duration_ms, then re-raise
    the original exception."""

    async def _noop_asgi_app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        return None

    boom_message = "handler exploded"

    async def _call_next_raising(_request: StarletteRequest) -> Response:
        raise _SentinelError(boom_message)

    middleware = AccessLogMiddleware(_noop_asgi_app)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/extract",
        "headers": [],
    }
    request = StarletteRequest(scope)  # type: ignore[arg-type] -- only Starlette attrs used

    with capture_logs() as cap_logs, pytest.raises(_SentinelError, match=boom_message):
        await middleware.dispatch(request, _call_next_raising)

    http_events = [e for e in cap_logs if e.get("event") == "http_request"]
    assert len(http_events) == 1, f"Expected 1 http_request event, got {len(http_events)}"
    log = http_events[0]
    assert log["method"] == "POST"
    assert log["path"] == "/api/v1/extract"
    assert log["status_code"] == 500
    assert isinstance(log["duration_ms"], float)
    assert log["duration_ms"] >= 0
    # Exception path must surface the exception type so operators can
    # distinguish failure modes (CancelledError on client disconnect vs.
    # real bugs) from the access log alone (issue #147).
    assert log["error"] == "_SentinelError"


async def test_access_log_middleware_logs_domain_error_http_status_on_exception() -> None:
    """When call_next raises a DomainError, the logged status_code must match
    the error's ``http_status`` (e.g., 404 for SkillNotFoundError), not the
    hardcoded 500 fallback. Protects SRE dashboards filtering ``status_code >=
    500`` from mislabeling 4xx domain errors as server errors if a future
    middleware reshuffle puts error mapping outside ``call_next`` (issue #238)."""

    async def _noop_asgi_app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        return None

    async def _call_next_raising(_request: StarletteRequest) -> Response:
        raise SkillNotFoundError(name="missing", version="1")

    middleware = AccessLogMiddleware(_noop_asgi_app)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/extract",
        "headers": [],
    }
    request = StarletteRequest(scope)  # type: ignore[arg-type] -- only Starlette attrs used

    with capture_logs() as cap_logs, pytest.raises(SkillNotFoundError):
        await middleware.dispatch(request, _call_next_raising)

    http_events = [e for e in cap_logs if e.get("event") == "http_request"]
    assert len(http_events) == 1
    log = http_events[0]
    assert log["status_code"] == SkillNotFoundError.http_status == 404
    assert log["error"] == "SkillNotFoundError"


async def test_access_log_middleware_logs_500_for_non_domain_exception() -> None:
    """Non-DomainError exceptions (including asyncio.CancelledError and other
    BaseException subclasses) fall back to ``status_code=500`` so operators
    still get a per-request record of the failure (issue #147 contract)."""

    async def _noop_asgi_app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        return None

    boom_message = "boom"

    async def _call_next_raising(_request: StarletteRequest) -> Response:
        raise _SentinelError(boom_message)

    middleware = AccessLogMiddleware(_noop_asgi_app)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/_raise",
        "headers": [],
    }
    request = StarletteRequest(scope)  # type: ignore[arg-type] -- only Starlette attrs used

    with capture_logs() as cap_logs, pytest.raises(_SentinelError):
        await middleware.dispatch(request, _call_next_raising)

    http_events = [e for e in cap_logs if e.get("event") == "http_request"]
    assert len(http_events) == 1
    assert http_events[0]["status_code"] == 500
    assert http_events[0]["error"] == "_SentinelError"


async def test_access_log_middleware_reraises_original_exception() -> None:
    """The original exception must propagate unchanged after the log is emitted."""

    async def _noop_asgi_app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        return None

    boom_message = "original error must propagate"

    async def _call_next_raising(_request: StarletteRequest) -> Response:
        raise _SentinelError(boom_message)

    middleware = AccessLogMiddleware(_noop_asgi_app)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/_raise",
        "headers": [],
    }
    request = StarletteRequest(scope)  # type: ignore[arg-type] -- only Starlette attrs used

    with pytest.raises(_SentinelError, match=boom_message):
        await middleware.dispatch(request, _call_next_raising)


async def test_access_log_middleware_emits_log_on_success() -> None:
    """Normal (non-exception) path still logs correctly."""
    application = FastAPI()
    application.add_middleware(AccessLogMiddleware)

    @application.get("/_ok")
    async def _ok() -> dict[str, str]:
        return {"status": "ok"}

    with capture_logs() as cap_logs:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as ac:
            response = await ac.get("/_ok")

    assert response.status_code == 200
    http_events = [e for e in cap_logs if e.get("event") == "http_request"]
    assert len(http_events) >= 1
    log = http_events[-1]
    assert log["method"] == "GET"
    assert log["path"] == "/_ok"
    assert log["status_code"] == 200
    assert isinstance(log["duration_ms"], float)
    assert log["duration_ms"] >= 0
    # Success path must NOT carry an `error` key — that field is reserved
    # for the exception path so downstream log processors can filter on
    # its presence (issue #147).
    assert "error" not in log

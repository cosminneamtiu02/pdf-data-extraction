"""Unit tests for AccessLogMiddleware."""

import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest
from starlette.types import Receive, Scope, Send

from app.api.access_log_middleware import AccessLogMiddleware


class _SentinelError(Exception):
    """Sentinel exception used only by the exception-propagation test."""


async def test_access_log_middleware_emits_log_on_unhandled_exception() -> None:
    """When call_next raises, the middleware must still emit an http_request
    log entry with status_code=500 and a positive duration_ms, then re-raise
    the original exception."""
    import structlog

    captured_events: list[dict[str, object]] = []

    def _capture_event(
        _logger: object, _method_name: str, event_dict: dict[str, object]
    ) -> dict[str, object]:
        captured_events.append(event_dict.copy())
        return event_dict

    structlog.configure(
        processors=[_capture_event, structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=False,
    )

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

    with pytest.raises(_SentinelError, match=boom_message):
        await middleware.dispatch(request, _call_next_raising)

    http_events = [e for e in captured_events if e.get("event") == "http_request"]
    assert len(http_events) == 1, f"Expected 1 http_request event, got {len(http_events)}"
    log = http_events[0]
    assert log["method"] == "POST"
    assert log["path"] == "/api/v1/extract"
    assert log["status_code"] == 500
    assert isinstance(log["duration_ms"], float)
    assert log["duration_ms"] >= 0


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
    import structlog

    captured_events: list[dict[str, object]] = []

    def _capture_event(
        _logger: object, _method_name: str, event_dict: dict[str, object]
    ) -> dict[str, object]:
        captured_events.append(event_dict.copy())
        return event_dict

    structlog.configure(
        processors=[_capture_event, structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=False,
    )

    application = FastAPI()
    application.add_middleware(AccessLogMiddleware)

    @application.get("/_ok")
    async def _ok() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as ac:
        response = await ac.get("/_ok")

    assert response.status_code == 200
    http_events = [e for e in captured_events if e.get("event") == "http_request"]
    assert len(http_events) >= 1
    log = http_events[-1]
    assert log["method"] == "GET"
    assert log["path"] == "/_ok"
    assert log["status_code"] == 200
    assert isinstance(log["duration_ms"], float)
    assert log["duration_ms"] >= 0

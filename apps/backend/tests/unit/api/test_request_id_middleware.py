"""Unit tests for RequestIdMiddleware (PDFX-E007-F003)."""

import re

import pytest
import structlog
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from app.api.request_id_middleware import RequestIdMiddleware

HEX32 = re.compile(r"^[a-f0-9]{32}$")


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)

    captured: list[dict] = []

    @application.get("/_test_log")
    async def _log_route() -> dict[str, str]:
        captured.append(dict(structlog.contextvars.get_contextvars()))
        return {"ok": "yes"}

    application.state.captured = captured
    return application


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_response_header_x_request_id_is_32_char_hex(app: FastAPI) -> None:
    async with await _client(app) as ac:
        response = await ac.get("/_test_log")
    header = response.headers.get("x-request-id")
    assert header is not None
    assert HEX32.match(header)


async def test_request_id_is_bound_to_contextvars_during_request(app: FastAPI) -> None:
    async with await _client(app) as ac:
        response = await ac.get("/_test_log")
    captured = app.state.captured[-1]
    assert "request_id" in captured
    assert captured["request_id"] == response.headers["x-request-id"]


async def test_request_id_is_unbound_after_request(app: FastAPI) -> None:
    async with await _client(app) as ac:
        await ac.get("/_test_log")
    assert "request_id" not in structlog.contextvars.get_contextvars()


async def test_x_request_id_present_on_domain_error_responses() -> None:
    """The middleware attaches X-Request-Id even when the handler raises a
    DomainError that is converted to a JSONResponse by the project's exception
    handler chain. Mount the real handlers so this exercises the production path,
    not a synthetic JSONResponse return.
    """
    from app.api.errors import register_exception_handlers
    from app.exceptions import NotFoundError

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    register_exception_handlers(application)

    @application.get("/_boom")
    async def _boom() -> dict[str, str]:
        raise NotFoundError

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as ac:
        response = await ac.get("/_boom")
    assert response.status_code == 404
    assert "x-request-id" in response.headers
    assert HEX32.match(response.headers["x-request-id"])
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


async def test_x_request_id_present_on_unhandled_500_responses() -> None:
    """X-Request-Id must appear on 500 responses produced by the catch-all
    ``handle_unhandled`` exception handler.  Before the fix, when ``call_next``
    raised an exception that was later caught by the exception handler, the
    middleware never got to set the header on the resulting response.
    """
    from app.api.errors import register_exception_handlers

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    register_exception_handlers(application)

    @application.get("/_unhandled")
    async def _unhandled() -> dict[str, str]:
        msg = "boom"
        raise RuntimeError(msg)

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/_unhandled")
    assert response.status_code == 500
    assert "x-request-id" in response.headers
    assert HEX32.match(response.headers["x-request-id"])
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


async def test_concurrent_requests_each_get_distinct_ids(app: FastAPI) -> None:
    import asyncio

    async with await _client(app) as ac:
        r1, r2 = await asyncio.gather(ac.get("/_test_log"), ac.get("/_test_log"))

    id1 = r1.headers["x-request-id"]
    id2 = r2.headers["x-request-id"]
    assert id1 != id2
    captured = app.state.captured
    captured_ids = {c["request_id"] for c in captured}
    assert {id1, id2}.issubset(captured_ids)


class _SentinelError(Exception):
    """Sentinel exception used only by the exception-propagation test below."""


async def test_original_exception_propagates_when_call_next_raises() -> None:
    """If `call_next` raises inside the middleware, the original error must
    propagate unchanged (not be masked by an `UnboundLocalError` or by any
    post-`finally` access to an unassigned `response`), and the `request_id`
    contextvar must still be unbound on the exception path.
    """
    from starlette.requests import Request as StarletteRequest
    from starlette.types import Receive, Scope, Send

    async def _noop_asgi_app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        return None

    boom_message = "original error"

    async def _call_next_raising(_request: StarletteRequest) -> Response:
        raise _SentinelError(boom_message)

    middleware = RequestIdMiddleware(_noop_asgi_app)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/_raise",
        "headers": [],
    }
    # BaseHTTPMiddleware.dispatch expects a fastapi.Request, but at runtime it
    # only uses Starlette Request attributes, so a StarletteRequest satisfies
    # the contract for this unit test.
    request = StarletteRequest(scope)  # type: ignore[arg-type]
    with pytest.raises(_SentinelError, match=boom_message):
        await middleware.dispatch(request, _call_next_raising)

    assert "request_id" not in structlog.contextvars.get_contextvars()

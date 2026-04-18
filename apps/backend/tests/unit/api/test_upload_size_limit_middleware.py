"""Unit tests for ``UploadSizeLimitMiddleware`` (issue #112).

The middleware rejects oversized uploads at the ASGI layer **before** the
route handler runs, preventing Starlette's multipart parser from spooling
the full request body into memory/disk on every oversized request.

Invariants under test:

* ``Content-Length`` greater than ``max_bytes`` on the guarded POST path
  produces a 413 ``PDF_TOO_LARGE`` envelope and the downstream app is
  **never** invoked.
* A missing ``Content-Length`` header on the guarded POST path (e.g. a
  chunked request) fails closed with the same 413 envelope — we cannot
  trust a body we have not yet seen the size of.
* Paths outside the guarded set pass through without header inspection.
* Requests with ``Content-Length`` equal to or below the configured limit
  pass through and reach the downstream app.
* Non-POST methods on the guarded path pass through (GET /api/v1/extract
  is not a real route, but the middleware must not interfere).
* A malformed ``Content-Length`` header (non-integer) fails closed.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.upload_size_limit_middleware import UploadSizeLimitMiddleware


def _build_app(max_bytes: int, *, guarded_paths: tuple[str, ...] = ("/api/v1/extract",)) -> FastAPI:
    """Build a minimal app with the middleware + a sentinel handler.

    The sentinel counts how many times it has been invoked so tests can
    assert the middleware short-circuited before the handler ran.
    """
    application = FastAPI()
    application.state.handler_calls = 0

    application.add_middleware(
        UploadSizeLimitMiddleware,
        max_bytes=max_bytes,
        guarded_paths=guarded_paths,
    )

    @application.post("/api/v1/extract")
    async def _extract() -> dict[str, Any]:
        application.state.handler_calls += 1
        return {"ok": True}

    @application.get("/healthz")
    async def _healthz() -> dict[str, str]:
        application.state.handler_calls += 1
        return {"status": "ok"}

    return application


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_rejects_when_content_length_exceeds_max_bytes() -> None:
    """A POST to the guarded path with CL > max_bytes returns 413 and never
    reaches the downstream handler."""
    app = _build_app(max_bytes=1024)

    async with _client(app) as ac:
        # httpx auto-derives Content-Length from ``content``'s length, so the
        # middleware sees an accurate numeric header without us setting one.
        response = await ac.post(
            "/api/v1/extract",
            content=b"x" * 2048,
            headers={"content-type": "multipart/form-data; boundary=b"},
        )

    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "PDF_TOO_LARGE"
    assert body["error"]["params"]["max_bytes"] == 1024
    assert body["error"]["params"]["actual_bytes"] == 2048
    assert body["error"]["details"] is None
    assert app.state.handler_calls == 0, "downstream handler must NOT be invoked"


async def test_accepts_when_content_length_equals_max_bytes() -> None:
    """CL == max_bytes is allowed through (strict greater-than check)."""
    app = _build_app(max_bytes=1024)

    async with _client(app) as ac:
        response = await ac.post(
            "/api/v1/extract",
            content=b"x" * 1024,
            headers={"content-type": "multipart/form-data; boundary=b"},
        )

    assert response.status_code == 200
    assert app.state.handler_calls == 1


async def test_accepts_when_content_length_below_max_bytes() -> None:
    """CL < max_bytes is allowed through."""
    app = _build_app(max_bytes=1024)

    async with _client(app) as ac:
        response = await ac.post(
            "/api/v1/extract",
            content=b"x" * 512,
            headers={"content-type": "multipart/form-data; boundary=b"},
        )

    assert response.status_code == 200
    assert app.state.handler_calls == 1


async def test_rejects_when_content_length_missing_on_guarded_path() -> None:
    """A guarded POST without Content-Length (e.g. chunked) fails closed.

    We cannot trust a body whose size we have not seen — admitting it would
    re-open the exact spool-then-reject window this middleware exists to
    close. Closing the door on chunked requests costs us nothing at the
    client layer (httpx / curl / browsers all emit CL for small uploads)
    and buys us a hard upper bound on ingress cost.
    """
    app = _build_app(max_bytes=1024)

    # Build a raw ASGI scope without Content-Length. httpx auto-inserts CL,
    # so we call the ASGI app directly.
    sent_messages: list[dict[str, Any]] = []

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/extract",
        "raw_path": b"/api/v1/extract",
        "query_string": b"",
        "headers": [
            (b"host", b"test"),
            (b"transfer-encoding", b"chunked"),
            (b"content-type", b"multipart/form-data; boundary=b"),
        ],
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
        "state": {},
    }

    await app(scope, _receive, _send)

    # Find the response.start message and confirm status=413.
    starts = [m for m in sent_messages if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    assert app.state.handler_calls == 0


async def test_rejects_when_content_length_is_malformed() -> None:
    """A non-integer Content-Length on the guarded path fails closed."""
    app = _build_app(max_bytes=1024)

    sent_messages: list[dict[str, Any]] = []

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/extract",
        "raw_path": b"/api/v1/extract",
        "query_string": b"",
        "headers": [
            (b"host", b"test"),
            (b"content-length", b"not-a-number"),
            (b"content-type", b"multipart/form-data; boundary=b"),
        ],
        "server": ("test", 80),
        "client": ("127.0.0.1", 12345),
        "state": {},
    }

    await app(scope, _receive, _send)

    starts = [m for m in sent_messages if m["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    assert app.state.handler_calls == 0


async def test_accepts_content_length_zero_on_guarded_path() -> None:
    """CL=0 is allowed through, so the middleware passes the request on."""
    app = _build_app(max_bytes=1024)

    async with _client(app) as ac:
        response = await ac.post(
            "/api/v1/extract",
            content=b"",
            headers={"content-type": "multipart/form-data; boundary=b"},
        )

    # The sentinel handler returns 200 with an empty body; the point is the
    # middleware passed through.
    assert response.status_code == 200
    assert app.state.handler_calls == 1


async def test_non_post_on_guarded_path_passes_through() -> None:
    """Non-POST methods on the guarded path are not subject to the size check.

    The issue is specifically about POST uploads spooling the body; GET and
    DELETE don't go through the multipart parser. Keeping the check narrowly
    scoped to POST avoids surprising side-effects on future routes that
    share the same path prefix.
    """
    app = _build_app(max_bytes=1024)

    # Mount a DELETE handler on the guarded path so we can send a request.
    @app.delete("/api/v1/extract")
    async def _delete() -> dict[str, bool]:
        app.state.handler_calls += 1
        return {"deleted": True}

    async with _client(app) as ac:
        response = await ac.delete(
            "/api/v1/extract",
            headers={"content-length": "999999"},
        )

    # The DELETE reaches the handler — the middleware did not inspect CL.
    assert response.status_code == 200
    assert app.state.handler_calls == 1


async def test_unguarded_path_passes_through_regardless_of_content_length() -> None:
    """A route outside ``guarded_paths`` is not subject to the size check."""
    app = _build_app(max_bytes=1024)

    async with _client(app) as ac:
        response = await ac.get(
            "/healthz",
            headers={"content-length": "999999"},
        )

    assert response.status_code == 200
    assert app.state.handler_calls == 1


async def test_middleware_sets_x_request_id_when_request_id_in_state() -> None:
    """When RequestIdMiddleware has run, the 413 response carries X-Request-Id."""
    from app.api.request_id_middleware import RequestIdMiddleware

    app = _build_app(max_bytes=1024)
    # RequestIdMiddleware must wrap UploadSizeLimitMiddleware to set state.
    app.add_middleware(RequestIdMiddleware)

    async with _client(app) as ac:
        response = await ac.post(
            "/api/v1/extract",
            content=b"x" * 2048,
            headers={"content-type": "multipart/form-data; boundary=b"},
        )

    assert response.status_code == 413
    assert "x-request-id" in response.headers
    body = response.json()
    assert body["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("max_bytes", [0, -1])
def test_constructor_rejects_non_positive_max_bytes(max_bytes: int) -> None:
    """max_bytes must be > 0 — Settings.max_pdf_bytes enforces ``gt=0``."""
    from fastapi import FastAPI as _FastAPI

    app = _FastAPI()
    with pytest.raises(ValueError, match="max_bytes"):
        UploadSizeLimitMiddleware(app, max_bytes=max_bytes, guarded_paths=("/x",))


async def test_middleware_rejects_chunked_transfer_encoding() -> None:
    """Transfer-Encoding: chunked on a guarded POST path is fail-closed.

    Even if a small Content-Length accompanies it (which would normally
    pass the size check), RFC 9110 §8.6 says TE wins — so the CL value is
    not a trustworthy size bound and the request must be rejected at the
    ASGI layer before the body is framed.
    """
    app = _build_app(max_bytes=1024)

    # Send the raw ASGI scope directly so we can attach both Content-Length
    # (within the limit) AND Transfer-Encoding: chunked. httpx normalizes
    # these away on the client side, so we exercise the middleware via a
    # crafted scope to prove the bypass is closed.
    sent: list[dict[str, object]] = []

    async def _send(msg: dict[str, object]) -> None:
        sent.append(msg)

    async def _receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/extract",
        "headers": [
            (b"content-length", b"10"),
            (b"transfer-encoding", b"chunked"),
            (b"content-type", b"multipart/form-data; boundary=b"),
        ],
        "state": {},
    }

    # Exercise the middleware as it is actually mounted on ``app`` by
    # ``_build_app`` — calling ``app(...)`` directly avoids wrapping the
    # app in a second middleware instance, which would hide any
    # regression in ``_build_app``'s wiring.
    await app(scope, _receive, _send)  # pyright: ignore[reportArgumentType]

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


async def test_middleware_rejects_duplicate_content_length_headers() -> None:
    """Two Content-Length headers are ambiguous framing and must be rejected.

    An attacker could set ``Content-Length: 10`` and ``Content-Length: 999999``
    and hope the guard picks the first. ``_parse_content_length`` returns
    None whenever the header appears more than once, so the middleware
    rejects the request with the unknown sentinel.
    """
    app = _build_app(max_bytes=1024)

    sent: list[dict[str, object]] = []

    async def _send(msg: dict[str, object]) -> None:
        sent.append(msg)

    async def _receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/extract",
        "headers": [
            (b"content-length", b"10"),
            (b"content-length", b"999999"),
            (b"content-type", b"multipart/form-data; boundary=b"),
        ],
        "state": {},
    }

    # Same rationale as the chunked-TE test: exercise the middleware as
    # mounted on ``app`` rather than constructing a second wrapper.
    await app(scope, _receive, _send)  # pyright: ignore[reportArgumentType]

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


async def test_middleware_generates_fallback_request_id_when_state_missing() -> None:
    """When RequestIdMiddleware has not run, _get_request_id falls back to
    a fresh uuid4().hex so the 413 envelope / X-Request-Id header always
    carry a valid 32-char correlation id (matching app.api.errors).
    """
    app = _build_app(max_bytes=1024)

    async with _client(app) as ac:
        response = await ac.post(
            "/api/v1/extract",
            content=b"x" * 2048,
            headers={"content-type": "multipart/form-data; boundary=b"},
        )

    assert response.status_code == 413
    # Header present, body field present, both equal, both 32-char hex.
    request_id = response.headers.get("x-request-id")
    assert request_id is not None
    assert len(request_id) == 32
    assert all(c in "0123456789abcdef" for c in request_id)
    assert response.json()["error"]["request_id"] == request_id

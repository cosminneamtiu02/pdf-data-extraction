"""UploadSizeLimitMiddleware — ASGI-level guard that rejects oversized uploads.

This middleware closes a DoS window exposed by Starlette's multipart
parser (issue #112). Before this guard existed, a POST to
``/api/v1/extract`` with a 500 MB body would be fully spooled into the
temporary file backing ``UploadFile`` **before** the route handler's
``read_with_byte_limit`` had a chance to reject it. The handler would then
return 413, but the server had already consumed 500 MB of memory/disk and
the network had already transferred 500 MB of bytes.

This middleware runs **before** route dispatch (which is what triggers the
multipart parser) and inspects ``Content-Length`` on the guarded POST
paths. Oversized or unknown sizes are rejected at the ASGI layer with the
same ``PDF_TOO_LARGE`` error envelope the downstream handler would have
produced, so the API contract is unchanged from the caller's perspective.

Fail-closed policy on missing ``Content-Length``
------------------------------------------------

Chunked transfer-encoded requests (``Transfer-Encoding: chunked``) do not
carry ``Content-Length``. This middleware treats a missing ``Content-Length``
on a guarded POST path as unsafe and rejects it. Admitting chunked bodies
would re-open the spool-then-reject window — we cannot trust the body size
until after we have read the body, which is exactly what we are trying to
avoid. Browsers and standard HTTP clients (``httpx``, ``requests``, ``curl``)
always send ``Content-Length`` for fixed-size uploads under normal
conditions, so fail-closed costs us nothing for the legitimate client
population.

The existing ``read_with_byte_limit`` inside the route handler is retained
as a defense-in-depth check: if a future deployment sits behind a proxy
that strips ``Content-Length`` or normalizes chunked-into-fixed, the
handler's chunked-read guard still caps the damage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import structlog
from starlette.responses import JSONResponse

from app.exceptions import PdfTooLargeError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

_logger = structlog.get_logger(__name__)

# Sentinel used when Content-Length is missing or malformed. Chosen as a very
# large int so the rejection-reporting path has a numeric value to include
# in ``params.actual_bytes`` without leaking anything about the underlying
# body (we have not read the body).
_UNKNOWN_CONTENT_LENGTH = -1


class UploadSizeLimitMiddleware:
    """Reject oversized uploads at the ASGI layer on the guarded POST paths.

    Parameters
    ----------
    app:
        The downstream ASGI application this middleware wraps.
    max_bytes:
        Maximum allowed ``Content-Length`` (strict greater-than check; an
        upload of exactly ``max_bytes`` is accepted). Must be positive to
        match ``Settings.max_pdf_bytes`` which is ``Field(gt=0)``.
    guarded_paths:
        Iterable of exact request paths on which ``POST`` requests are
        subject to the size check. A tuple of paths (rather than a single
        path) keeps the class reusable if another upload route is added
        later; the alternative of inspecting ``scope["route"]`` is not
        possible at this layer because route matching happens downstream.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        guarded_paths: Iterable[str],
    ) -> None:
        if max_bytes <= 0:
            msg = f"max_bytes must be positive; got {max_bytes}"
            raise ValueError(msg)
        self._app = app
        self._max_bytes = max_bytes
        # Materialize into a frozenset for O(1) path lookup and
        # constructor-time immutability against accidental mutation of a
        # list passed by caller.
        self._guarded_paths = frozenset(guarded_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_guarded(scope):
            await self._app(scope, receive, send)
            return

        content_length = _parse_content_length(scope)
        if content_length is None:
            await self._reject(scope, send, actual_bytes=_UNKNOWN_CONTENT_LENGTH)
            return
        if content_length > self._max_bytes:
            await self._reject(scope, send, actual_bytes=content_length)
            return

        await self._app(scope, receive, send)

    def _is_guarded(self, scope: Scope) -> bool:
        """Return True if this request is subject to the size check.

        Only ``http`` scopes with ``method == POST`` on a guarded path are
        inspected. WebSocket and lifespan scopes pass through unchanged, as
        do non-POST methods on the guarded path (they do not trigger the
        multipart parser).
        """
        if scope.get("type") != "http":
            return False
        if scope.get("method") != "POST":
            return False
        return scope.get("path") in self._guarded_paths

    async def _reject(self, scope: Scope, send: Send, *, actual_bytes: int) -> None:
        """Emit the ``PDF_TOO_LARGE`` error envelope at the ASGI layer.

        The envelope shape matches what
        ``app.api.errors.register_exception_handlers`` produces for a
        ``PdfTooLargeError`` raised from the handler, so callers see an
        identical response regardless of which layer rejected them.

        When ``RequestIdMiddleware`` has already run (i.e. it is registered
        outside this one in ``configure_middleware``), ``scope["state"]``
        carries the generated 32-char hex request id and we reuse it so the
        ``X-Request-Id`` header and the ``error.request_id`` field stay
        consistent. When it has not run (e.g. in unit tests that mount this
        middleware directly on a bare FastAPI app without RequestId), we
        omit the header and set the body field to ``None`` — the caller
        still gets a well-formed envelope, just without the correlation id.
        """
        err = PdfTooLargeError(max_bytes=self._max_bytes, actual_bytes=actual_bytes)
        request_id = _get_request_id(scope)

        _logger.warning(
            "upload_rejected_oversized",
            path=scope.get("path"),
            max_bytes=self._max_bytes,
            actual_bytes=actual_bytes,
            request_id=request_id,
        )

        content: dict[str, object] = {
            "error": {
                "code": err.code,
                "params": err.params.model_dump() if err.params else {},
                "details": None,
                "request_id": request_id,
            },
        }

        headers: dict[str, str] = {}
        if request_id is not None:
            headers["X-Request-Id"] = request_id

        response = JSONResponse(
            status_code=err.http_status,
            content=content,
            headers=headers,
        )
        # JSONResponse is itself an ASGI app; calling it with a no-op
        # receive is safe because it never reads the request body.
        await response(scope, _empty_receive, send)


async def _empty_receive() -> Message:
    """No-op ``receive`` for the early-response path.

    ``Response.__call__`` does not consume the request body, but the ASGI
    signature requires a callable; this never-returning stub satisfies the
    type without ever being awaited in practice.
    """
    return {"type": "http.disconnect"}


def _parse_content_length(scope: Scope) -> int | None:
    """Return the integer ``Content-Length`` header, or None if missing/malformed.

    ASGI spec: ``scope["headers"]`` is a list of ``(bytes, bytes)`` pairs
    with lowercase header names. Returning ``None`` for both the missing
    and malformed cases lets the caller apply a single fail-closed policy.
    Negative values are also treated as missing — a negative Content-Length
    is not meaningful and should not be admitted.
    """
    raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for name, value in raw_headers:
        if name == b"content-length":
            try:
                parsed = int(value.decode("latin-1"))
            except (ValueError, UnicodeDecodeError):
                return None
            if parsed < 0:
                return None
            return parsed
    return None


def _get_request_id(scope: Scope) -> str | None:
    """Return the 32-char hex request id set by ``RequestIdMiddleware``, or None.

    ``RequestIdMiddleware`` assigns ``request.state.request_id``, which
    Starlette backs with ``scope["state"]``. When this middleware runs
    outside a stack that includes ``RequestIdMiddleware`` (e.g. a direct
    unit-test mount), ``scope["state"]`` is absent and we return ``None``
    — the error envelope then reports ``request_id: null``.
    """
    # ``scope["state"]`` is ``dict[str, Any]`` in the ASGI spec, but pyright
    # strict narrows it to ``dict[Unknown, Unknown]`` after isinstance, so we
    # re-type explicitly with ``cast`` before indexing.
    state_obj = scope.get("state")
    if not isinstance(state_obj, dict):
        return None
    state = cast("dict[str, object]", state_obj)
    request_id = state.get("request_id")
    if isinstance(request_id, str):
        return request_id
    return None

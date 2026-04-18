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
from uuid import uuid4

import structlog
from starlette.responses import JSONResponse

from app.exceptions import PdfTooLargeError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

_logger = structlog.get_logger(__name__)

# Sentinel used when Content-Length is missing, malformed, or ambiguous
# (e.g. chunked transfer-encoding or duplicate Content-Length headers). The
# negative value communicates "unknown body size" to the rejection-reporting
# path so ``params.actual_bytes`` stays numeric without implying we read or
# measured the body. Clients should treat ``-1`` as "unknown", not as a real
# byte count.
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

        # Chunked requests advertise body size indirectly through
        # Transfer-Encoding rather than Content-Length. Even if Content-Length
        # is also present, RFC 9110 §8.6 says Transfer-Encoding wins — so a
        # caller who sends both headers could bypass a pure CL-based guard.
        # Fail closed on either: chunked transfer-encoding OR duplicate
        # Content-Length headers get rejected with the unknown sentinel.
        if _has_chunked_transfer_encoding(scope):
            await self._reject(scope, send, actual_bytes=_UNKNOWN_CONTENT_LENGTH)
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

        ``_get_request_id`` guarantees a 32-char hex string — either the
        one set by ``RequestIdMiddleware`` (when it ran upstream) or a
        fresh ``uuid4().hex`` fallback when this middleware is mounted
        directly (e.g. unit tests). Either way the envelope and
        ``X-Request-Id`` header always carry a valid correlation id,
        matching the fallback in ``app.api.errors._get_request_id``.
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

        response = JSONResponse(
            status_code=err.http_status,
            content=content,
            headers={"X-Request-Id": request_id},
        )
        # JSONResponse is itself an ASGI app; calling it with a no-op
        # receive is safe because it never reads the request body.
        await response(scope, _empty_receive, send)


async def _empty_receive() -> Message:
    """Terminal ``receive`` callable for the early-response path.

    ``Response.__call__`` does not normally consume the request body here,
    but the ASGI signature still requires a ``receive`` callable. Return a
    terminal empty ``http.request`` message rather than ``http.disconnect``
    so any consumer reading the body sees "no more data" instead of an
    unexpected disconnect, which some frameworks treat as a client abort.
    """
    return {"type": "http.request", "body": b"", "more_body": False}


def _has_chunked_transfer_encoding(scope: Scope) -> bool:
    """Return True iff any ``Transfer-Encoding`` header contains ``chunked``.

    Per RFC 9110 §8.6, ``Transfer-Encoding`` takes precedence over
    ``Content-Length`` when both are present: a request can set
    ``Content-Length: 100`` plus ``Transfer-Encoding: chunked`` and the
    server MUST use chunked framing, meaning the CL value is not a
    trustworthy size bound. Reject the request outright so the guard
    cannot be bypassed by header combination.
    """
    raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for name, value in raw_headers:
        if name != b"transfer-encoding":
            continue
        # Transfer-Encoding is a comma-separated list of codings; ``chunked``
        # may appear alongside ``gzip`` etc. A case-insensitive substring
        # match on ``chunked`` is the safe conservative check.
        if b"chunked" in value.lower():
            return True
    return False


def _parse_content_length(scope: Scope) -> int | None:
    """Return the integer ``Content-Length`` header, or None on any ambiguity.

    Returns ``None`` when:
    - the header is absent;
    - the value is malformed (non-numeric, negative);
    - multiple ``Content-Length`` headers are present (RFC 9110 requires
      the server to reject ambiguous framing; returning None here makes the
      caller's fail-closed policy apply).

    ASGI spec: ``scope["headers"]`` is a list of ``(bytes, bytes)`` pairs
    with lowercase header names.
    """
    raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    cl_values: list[bytes] = [value for name, value in raw_headers if name == b"content-length"]
    if len(cl_values) != 1:
        return None
    try:
        parsed = int(cl_values[0].decode("latin-1"))
    except (ValueError, UnicodeDecodeError):
        return None
    if parsed < 0:
        return None
    return parsed


def _get_request_id(scope: Scope) -> str:
    """Return the 32-char hex request id set by ``RequestIdMiddleware``.

    Falls back to a fresh ``uuid4().hex`` when the middleware is absent so
    the ``X-Request-Id`` header and the ``error.request_id`` field always
    carry a valid 32-char hex string — matching the fallback in
    ``app.api.errors._get_request_id`` so every rejection layer produces the
    same envelope / header contract.
    """
    # ``scope["state"]`` is ``dict[str, Any]`` in the ASGI spec, but pyright
    # strict narrows it to ``dict[Unknown, Unknown]`` after isinstance, so we
    # re-type explicitly with ``cast`` before indexing.
    state_obj = scope.get("state")
    if isinstance(state_obj, dict):
        state = cast("dict[str, object]", state_obj)
        request_id = state.get("request_id")
        if isinstance(request_id, str):
            return request_id
    return uuid4().hex

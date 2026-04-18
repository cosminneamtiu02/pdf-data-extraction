"""Middleware wiring — assembles the full middleware stack onto the FastAPI app.

Individual middleware classes live one-per-file in this package
(`request_id_middleware.py`, `access_log_middleware.py`,
`upload_size_limit_middleware.py`) per CLAUDE.md's "one class per file" rule.
This module only owns the stack assembly order.

Registration order vs. execution order
--------------------------------------
Starlette/FastAPI's ``app.add_middleware`` *prepends* to the middleware stack,
so the order of ``add_middleware`` calls in :func:`configure_middleware` is
the **reverse** of the runtime execution order. The desired execution order
(outermost first) is::

    CORS -> RequestId -> AccessLog -> UploadSizeLimit -> route handler

which means the calls in :func:`configure_middleware` must be registered in
the opposite sequence (``UploadSizeLimit`` first, ``CORS`` last). Do not
reorder those calls without also flipping this expectation — getting it
wrong silently changes which middleware sees the request first and breaks
CORS preflight, request-id propagation, access-log correlation, or the
upload-size guard's ability to read the request id set by RequestId.

UploadSizeLimit placement rationale (issue #112)
------------------------------------------------
The upload-size guard runs innermost (just before route dispatch) because:

* It needs the request id from ``RequestIdMiddleware`` in its rejection
  envelope, so RequestId must wrap it (RequestId registers AFTER and runs
  outside at runtime).
* Its rejection must be surfaced in the access log like any other response,
  so AccessLog must wrap it (AccessLog registers AFTER and runs outside).
* It must run BEFORE FastAPI's route dispatch — which is when Starlette's
  multipart parser would otherwise spool the upload. Registering this
  middleware first (innermost) is what delivers that guarantee.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.access_log_middleware import AccessLogMiddleware
from app.api.request_id_middleware import RequestIdMiddleware
from app.api.upload_size_limit_middleware import UploadSizeLimitMiddleware

# The set of POST paths whose request body is a PDF upload. Keep this
# co-located with the middleware wiring (rather than in the router) because
# ``configure_middleware`` is the only place that needs it — the router
# continues to rely on its own ``read_with_byte_limit`` for
# defense-in-depth. Both the canonical path and its trailing-slash variant
# are listed so the guard survives FastAPI's default redirect_slashes
# behavior (a ``/api/v1/extract/`` request would otherwise bypass the
# ASGI guard and fall through to the slower in-handler limit). Extend
# this tuple when a second upload route is added.
_GUARDED_UPLOAD_PATHS: tuple[str, ...] = (
    "/api/v1/extract",
    "/api/v1/extract/",
)


def configure_middleware(
    app: FastAPI,
    cors_origins: list[str],
    *,
    max_upload_bytes: int,
) -> None:
    """Attach all middleware to the FastAPI app.

    Execution order (outermost first), once Starlette has built the stack::

        1. CORS             (handles preflight before anything else)
        2. RequestId        (sets request_id for all downstream middleware)
        3. AccessLog        (logs after response, reads request_id from contextvars)
        4. UploadSizeLimit  (rejects oversized uploads before route dispatch)

    The ``add_middleware`` calls below appear in the **reverse** of that
    execution order because Starlette/FastAPI prepends each newly added
    middleware to the front of the stack. See the module docstring for why
    flipping these calls breaks CORS preflight, request-id propagation,
    or the upload-size guard (issue #112).
    """
    app.add_middleware(
        UploadSizeLimitMiddleware,
        max_bytes=max_upload_bytes,
        guarded_paths=_GUARDED_UPLOAD_PATHS,
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

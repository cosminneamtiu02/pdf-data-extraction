"""Middleware wiring — assembles the full middleware stack onto the FastAPI app.

Individual middleware classes live one-per-file in this package
(`request_id_middleware.py`, `access_log_middleware.py`) per CLAUDE.md's
"one class per file" rule. This module only owns the stack assembly order.

Registration order vs. execution order
--------------------------------------
Starlette/FastAPI's ``app.add_middleware`` *prepends* to the middleware stack,
so the order of ``add_middleware`` calls in :func:`configure_middleware` is
the **reverse** of the runtime execution order. The desired execution order
(outermost first) is::

    CORS -> RequestId -> AccessLog -> route handler

which means the calls in :func:`configure_middleware` must be registered in
the opposite sequence (``AccessLog`` first, ``CORS`` last). Do not reorder
those calls without also flipping this expectation — getting it wrong silently
changes which middleware sees the request first and breaks CORS preflight,
request-id propagation, and access-log correlation.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.access_log_middleware import AccessLogMiddleware
from app.api.request_id_middleware import RequestIdMiddleware


def configure_middleware(app: FastAPI, cors_origins: list[str]) -> None:
    """Attach all middleware to the FastAPI app.

    Execution order (outermost first), once Starlette has built the stack::

        1. CORS         (handles preflight before anything else)
        2. RequestId    (sets request_id for all downstream middleware and handlers)
        3. AccessLog    (logs after response is generated, reads request_id from contextvars)

    The ``add_middleware`` calls below appear in the **reverse** of that
    execution order because Starlette/FastAPI prepends each newly added
    middleware to the front of the stack. See the module docstring for why
    flipping these calls breaks CORS preflight and request-id propagation.
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

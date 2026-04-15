"""Middleware wiring — assembles the full middleware stack onto the FastAPI app.

Individual middleware classes live one-per-file in this package
(`request_id_middleware.py`, `access_log_middleware.py`) per CLAUDE.md's
"one class per file" rule. This module only owns the stack assembly order.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.access_log_middleware import AccessLogMiddleware
from app.api.request_id_middleware import RequestIdMiddleware


def configure_middleware(app: FastAPI, cors_origins: list[str]) -> None:
    """Attach all middleware to the FastAPI app.

    Order matters — outermost middleware runs first. The stack from outside in:
    1. CORS (handles preflight before anything else)
    2. RequestId (sets request_id for all downstream middleware and handlers)
    3. AccessLog (logs after response is generated, includes request_id from contextvars)
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

"""Unit tests for :func:`configure_middleware` — middleware registration order.

The module docstring of ``app/api/middleware.py`` declares the runtime
execution order as::

    CORS (outermost) -> RequestId -> AccessLog (innermost) -> route handler

Getting this order wrong silently breaks CORS preflight, request-id
propagation, and access-log correlation (issue #154). These tests lock in
the invariant so a future refactor that reorders the ``add_middleware`` calls
fails CI rather than slipping through silently.

Starlette's ``build_middleware_stack`` iterates ``self.user_middleware`` in
*reverse* when wrapping the app, so ``user_middleware[0]`` is the outermost
at runtime and ``user_middleware[-1]`` is innermost. Asserting the list order
directly is therefore equivalent to asserting the runtime execution order —
no need to spin up a live server to observe dispatch order.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.access_log_middleware import AccessLogMiddleware
from app.api.middleware import configure_middleware
from app.api.request_id_middleware import RequestIdMiddleware


def test_configure_middleware_registers_expected_execution_order() -> None:
    """Runtime order is CORS (outermost) -> RequestId -> AccessLog (innermost).

    ``app.user_middleware`` holds the registrations in the order Starlette will
    use to build the stack: index 0 becomes outermost, index ``-1`` becomes
    innermost. The docstring of ``configure_middleware`` commits to exactly
    this order.
    """
    app = FastAPI()

    configure_middleware(app, cors_origins=["http://localhost"])

    registered_classes = [m.cls for m in app.user_middleware]
    assert registered_classes == [
        CORSMiddleware,
        RequestIdMiddleware,
        AccessLogMiddleware,
    ], (
        "Middleware execution order must be CORS (outermost) -> RequestId -> "
        "AccessLog (innermost). Starlette builds the stack by iterating "
        "user_middleware in reverse, so user_middleware[0] is outermost. "
        f"Got: {[c.__name__ for c in registered_classes]}"
    )


def test_cors_is_outermost_so_preflight_bypasses_inner_middleware() -> None:
    """CORS must sit at index 0 so OPTIONS preflight never reaches inner layers.

    If ``CORSMiddleware`` were registered deeper in the chain, preflight
    requests would be processed by ``RequestIdMiddleware`` and
    ``AccessLogMiddleware`` first, which changes observed request-id and
    access-log behaviour for a class of requests that never produce a
    route-handler response. This assertion is intentionally named to make
    the reason a refactor broke the test self-explanatory.
    """
    app = FastAPI()

    configure_middleware(app, cors_origins=["http://localhost"])

    assert app.user_middleware[0].cls is CORSMiddleware, (
        "CORSMiddleware must be registered first (outermost) so preflight "
        "OPTIONS requests are handled before any other middleware sees them."
    )


def test_request_id_runs_before_access_log_so_log_has_correlation_id() -> None:
    """RequestId must sit OUTSIDE AccessLog so the access log can read the id.

    ``AccessLogMiddleware`` reads the request-id from a contextvar that
    ``RequestIdMiddleware`` sets. If AccessLog were outside RequestId, the
    contextvar would be unset when the access-log entry is emitted and every
    log line would be missing its correlation id. The test asserts
    ``user_middleware`` index ordering because that is what Starlette uses to
    build the stack (see module docstring).
    """
    app = FastAPI()

    configure_middleware(app, cors_origins=["http://localhost"])

    classes = [m.cls for m in app.user_middleware]
    request_id_index = classes.index(RequestIdMiddleware)
    access_log_index = classes.index(AccessLogMiddleware)
    assert request_id_index < access_log_index, (
        f"RequestIdMiddleware (index {request_id_index}) must be registered "
        f"before AccessLogMiddleware (index {access_log_index}) so it wraps "
        f"AccessLog at runtime, making the request-id contextvar available "
        f"when access-log entries are emitted."
    )

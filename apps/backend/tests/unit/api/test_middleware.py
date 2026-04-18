"""Unit tests for :func:`configure_middleware` — middleware registration order.

The module docstring of ``app/api/middleware.py`` declares the runtime
execution order as::

    CORS (outermost) -> RequestId -> AccessLog -> UploadSizeLimit (innermost)
        -> route handler

Getting this order wrong silently breaks CORS preflight, request-id
propagation, access-log correlation (issue #154), or the ASGI-level
upload-size guard (issue #112). These tests lock in the invariant so a
future refactor that reorders the ``add_middleware`` calls fails CI rather
than slipping through silently.

Starlette's ``build_middleware_stack`` iterates ``self.user_middleware`` in
*reverse* when wrapping the app, so ``user_middleware[0]`` is the outermost
at runtime and ``user_middleware[-1]`` is innermost. Asserting the list order
directly is therefore equivalent to asserting the runtime execution order —
no need to spin up a live server to observe dispatch order.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.api.access_log_middleware import AccessLogMiddleware
from app.api.middleware import configure_middleware
from app.api.request_id_middleware import RequestIdMiddleware
from app.api.upload_size_limit_middleware import UploadSizeLimitMiddleware

# Minimal CORS allowlists for the registration-order tests. These tests do
# not care what the actual allowed methods/headers are — only the middleware
# stacking order is under test — but ``configure_middleware`` now requires
# both keyword args so the call site cannot silently re-introduce a wildcard
# default (issue #211).
_TEST_CORS_METHODS = ["GET", "POST"]
_TEST_CORS_HEADERS = ["Content-Type"]


def test_configure_middleware_registers_expected_execution_order() -> None:
    """Runtime order is CORS -> RequestId -> AccessLog -> UploadSizeLimit.

    ``app.user_middleware`` holds the registrations in the order Starlette will
    use to build the stack: index 0 becomes outermost, index ``-1`` becomes
    innermost. The docstring of ``configure_middleware`` commits to exactly
    this order.
    """
    app = FastAPI()

    configure_middleware(
        app,
        cors_origins=["http://localhost"],
        max_upload_bytes=50 * 1024 * 1024,
        cors_methods=_TEST_CORS_METHODS,
        cors_headers=_TEST_CORS_HEADERS,
    )

    registered_classes = [m.cls for m in app.user_middleware]
    assert registered_classes == [
        CORSMiddleware,
        RequestIdMiddleware,
        AccessLogMiddleware,
        UploadSizeLimitMiddleware,
    ], (
        "Middleware execution order must be CORS (outermost) -> RequestId -> "
        "AccessLog -> UploadSizeLimit (innermost). Starlette builds the stack "
        "by iterating user_middleware in reverse, so user_middleware[0] is "
        f"outermost. Got: {[c.__name__ for c in registered_classes]}"
    )


def test_upload_size_limit_is_innermost_so_it_runs_before_route_dispatch() -> None:
    """UploadSizeLimit must sit LAST (innermost) so it gates multipart parsing.

    The whole point of the middleware (issue #112) is to reject oversized
    uploads BEFORE FastAPI's route dispatcher fires, which is when
    Starlette's multipart parser otherwise spools the upload body. Moving
    it earlier in the registration list (i.e. outer in runtime) would not
    break its behaviour in the common case, but moving it AFTER RequestId
    would starve its rejection envelope of a correlation id.
    """
    app = FastAPI()

    configure_middleware(
        app,
        cors_origins=["http://localhost"],
        max_upload_bytes=1024,
        cors_methods=_TEST_CORS_METHODS,
        cors_headers=_TEST_CORS_HEADERS,
    )

    classes = [m.cls for m in app.user_middleware]
    upload_index = classes.index(UploadSizeLimitMiddleware)
    request_id_index = classes.index(RequestIdMiddleware)
    assert upload_index == len(classes) - 1, (
        f"UploadSizeLimitMiddleware must be registered last (innermost) so "
        f"it runs before FastAPI's route dispatcher. Got index {upload_index} "
        f"out of {len(classes)}."
    )
    assert request_id_index < upload_index, (
        f"RequestIdMiddleware (index {request_id_index}) must be registered "
        f"before UploadSizeLimitMiddleware (index {upload_index}) so the "
        f"guard's rejection envelope can reuse the request id from "
        f"scope['state']."
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

    configure_middleware(
        app,
        cors_origins=["http://localhost"],
        max_upload_bytes=50 * 1024 * 1024,
        cors_methods=_TEST_CORS_METHODS,
        cors_headers=_TEST_CORS_HEADERS,
    )

    assert app.user_middleware[0].cls is CORSMiddleware, (
        "CORSMiddleware must be registered first (outermost) so preflight "
        "OPTIONS requests are handled before any other middleware sees them."
    )


def test_cors_methods_and_headers_are_narrowed_not_wildcarded() -> None:
    """CORS ``allow_methods`` and ``allow_headers`` must honour explicit lists.

    Hardcoding ``["*"]`` in ``configure_middleware`` accepted any verb and
    any header (including ``Authorization``), regardless of how carefully
    an operator scoped ``cors_origins``. ``configure_middleware`` now takes
    keyword-only ``cors_methods`` / ``cors_headers`` and forwards them to
    ``CORSMiddleware`` verbatim. Issue #211.
    """
    app = FastAPI()

    configure_middleware(
        app,
        cors_origins=["http://localhost"],
        max_upload_bytes=50 * 1024 * 1024,
        cors_methods=["GET", "POST"],
        cors_headers=["Authorization", "Content-Type"],
    )

    cors_mw = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert cors_mw.kwargs["allow_methods"] == ["GET", "POST"], (
        "allow_methods must reflect the caller's list verbatim, "
        f"not a wildcard. Got: {cors_mw.kwargs['allow_methods']!r}"
    )
    assert cors_mw.kwargs["allow_headers"] == ["Authorization", "Content-Type"], (
        "allow_headers must reflect the caller's list verbatim, "
        f"not a wildcard. Got: {cors_mw.kwargs['allow_headers']!r}"
    )


def test_cors_preflight_rejects_disallowed_method() -> None:
    """A preflight for a verb NOT in ``cors_methods`` must not advertise it.

    The unit-level check above (``test_cors_methods_and_headers_are_narrowed_not_wildcarded``)
    verifies that ``configure_middleware`` forwards the caller's list into
    ``CORSMiddleware.kwargs`` — it does not observe the behaviour of the
    middleware itself. This test drives a real OPTIONS preflight through
    Starlette's ``CORSMiddleware`` via ``TestClient`` and asserts that a
    ``DELETE`` verb (absent from the allowlist) does NOT appear in
    ``Access-Control-Allow-Methods``, so browsers will correctly block the
    follow-up request. Together with the unit assertion, this closes the
    plumbing-vs-behaviour gap the Copilot reviewer flagged on issue #211.
    """
    app = FastAPI()
    configure_middleware(
        app,
        cors_origins=["http://localhost"],
        max_upload_bytes=1024,
        cors_methods=["GET", "POST"],
        cors_headers=["Content-Type"],
    )
    client = TestClient(app)

    response = client.options(
        "/nowhere",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "DELETE",
        },
    )

    # Starlette's CORSMiddleware either short-circuits with 400 or responds
    # without an ``Access-Control-Allow-Methods`` header containing DELETE.
    # Either shape is acceptable; what matters is that DELETE is not
    # advertised as allowed.
    allow_methods = response.headers.get("access-control-allow-methods", "")
    assert "DELETE" not in allow_methods, (
        "Preflight for a disallowed verb must not surface that verb in "
        f"Access-Control-Allow-Methods. Got: {allow_methods!r}"
    )


def test_cors_preflight_allows_listed_method() -> None:
    """A preflight for a verb IN ``cors_methods`` is accepted by the middleware.

    Companion to ``test_cors_preflight_rejects_disallowed_method`` — without
    this positive case, a regression that disabled CORS handling entirely
    would satisfy the negative test trivially. The observable signal is
    ``Access-Control-Allow-Origin`` echoing the request Origin, which
    ``CORSMiddleware`` emits only when the preflight passes its verb + header
    + origin check.
    """
    app = FastAPI()
    configure_middleware(
        app,
        cors_origins=["http://localhost"],
        max_upload_bytes=1024,
        cors_methods=["GET", "POST"],
        cors_headers=["Content-Type"],
    )
    client = TestClient(app)

    response = client.options(
        "/nowhere",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "POST",
        },
    )

    allow_origin = response.headers.get("access-control-allow-origin", "")
    assert allow_origin == "http://localhost", (
        "Preflight for an allowed verb must echo the request Origin in "
        f"Access-Control-Allow-Origin. Got: {allow_origin!r}"
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

    configure_middleware(
        app,
        cors_origins=["http://localhost"],
        max_upload_bytes=50 * 1024 * 1024,
        cors_methods=_TEST_CORS_METHODS,
        cors_headers=_TEST_CORS_HEADERS,
    )

    classes = [m.cls for m in app.user_middleware]
    request_id_index = classes.index(RequestIdMiddleware)
    access_log_index = classes.index(AccessLogMiddleware)
    assert request_id_index < access_log_index, (
        f"RequestIdMiddleware (index {request_id_index}) must be registered "
        f"before AccessLogMiddleware (index {access_log_index}) so it wraps "
        f"AccessLog at runtime, making the request-id contextvar available "
        f"when access-log entries are emitted."
    )

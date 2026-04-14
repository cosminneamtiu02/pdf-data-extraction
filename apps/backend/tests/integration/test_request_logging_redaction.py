"""Integration tests for request-id correlation and structured-log redaction.

These tests mount transient routes onto the real FastAPI app to drive log
emissions through the configured structlog processor chain (which the app
factory in app.main wires from Settings). They prove the LogRedactionFilter
is actually installed, not just unit-tested in isolation.
"""

import asyncio
import io
import logging
import re
from collections.abc import AsyncIterator, Iterator

import pytest
import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.main import app

HEX32 = re.compile(r"^[a-f0-9]{32}$")
SENTINEL = "SENSITIVE_PAYLOAD_42"
PDF_CANARY = "CANARY_BYTES_XYZ"

_TEST_PATHS = {"/_t/log_safe", "/_t/log_sensitive", "/_t/log_pdf", "/_t/log_error"}


@pytest.fixture
def test_app() -> Iterator[FastAPI]:
    """The real app with extra logging routes mounted for the duration of the test."""
    log = structlog.get_logger("redaction_test")

    @app.get("/_t/log_safe")
    async def _safe() -> dict[str, str]:
        log.info("safe_event", skill_name="invoice", duration_ms=123)
        return {"ok": "yes"}

    @app.get("/_t/log_sensitive")
    async def _sensitive() -> dict[str, str]:
        log.info("sensitive_event", extracted_value=SENTINEL)
        return {"ok": "yes"}

    @app.get("/_t/log_pdf")
    async def _pdf() -> dict[str, str]:
        log.info("parse_event", pdf_bytes=PDF_CANARY.encode())
        return {"ok": "yes"}

    @app.get("/_t/log_error")
    async def _error() -> JSONResponse:
        log.error(
            "error_event",
            error_code="VALIDATION_FAILED",
            extracted_value=SENTINEL,
            prompt="ignore me",
        )
        return JSONResponse(status_code=400, content={"error": "bad"})

    yield app

    app.router.routes = [
        r for r in app.router.routes if getattr(r, "path", None) not in _TEST_PATHS
    ]


@pytest.fixture
async def client(test_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _capture_logs(client: AsyncClient, path: str) -> tuple[str, str]:
    """Issue a request and return (response_header_x_request_id, captured_log_output)."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        response = await client.get(path)
    finally:
        root.removeHandler(handler)
        handler.flush()
    return response.headers.get("x-request-id", ""), buf.getvalue()


async def test_health_response_has_hex_request_id_header(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert HEX32.match(response.headers["x-request-id"])


async def test_log_record_carries_request_id_matching_header(client: AsyncClient) -> None:
    request_id, captured = await _capture_logs(client, "/_t/log_safe")
    assert HEX32.match(request_id)
    assert request_id in captured


async def test_concurrent_requests_have_isolated_request_ids(client: AsyncClient) -> None:
    r1, r2 = await asyncio.gather(client.get("/_t/log_safe"), client.get("/_t/log_safe"))
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]
    assert HEX32.match(r1.headers["x-request-id"])
    assert HEX32.match(r2.headers["x-request-id"])


async def test_extracted_value_sentinel_never_appears_in_logs(client: AsyncClient) -> None:
    _, captured = await _capture_logs(client, "/_t/log_sensitive")
    assert SENTINEL not in captured


async def test_pdf_bytes_canary_never_appears_in_logs(client: AsyncClient) -> None:
    _, captured = await _capture_logs(client, "/_t/log_pdf")
    assert PDF_CANARY not in captured


async def test_error_log_keeps_error_code_but_strips_sensitive_fields(
    client: AsyncClient,
) -> None:
    _, captured = await _capture_logs(client, "/_t/log_error")
    assert "VALIDATION_FAILED" in captured  # allowlisted key survives
    assert SENTINEL not in captured  # extracted_value stripped
    assert "ignore me" not in captured  # prompt stripped

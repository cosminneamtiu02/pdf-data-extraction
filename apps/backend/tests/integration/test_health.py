"""Integration tests for health, readiness, middleware, and CORS.

The /ready endpoint is gated on a TTL-cached Ollama probe (PDFX-E007-F001).
These tests override the ``get_probe_cache`` dependency to inject a
controllable ``ProbeCache`` with a ``FakeProbe``, so no real Ollama is needed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_probe_cache
from app.api.probe_cache import ProbeCache
from app.main import app
from tests.conftest import FakeProbe

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cache(
    results: list[bool],
    ttl: float = 60.0,
) -> tuple[ProbeCache, FakeProbe]:
    probe = FakeProbe(results=results)
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=ttl,
    )
    return cache, probe


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncClient:
    """HTTP client bound to the FastAPI ASGI app in-process (no DB, no network)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


async def test_health_returns_200(client: AsyncClient) -> None:
    """GET /health should return 200 with status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /ready — probe returning True
# ---------------------------------------------------------------------------


async def test_ready_returns_200_when_probe_ok(client: AsyncClient) -> None:
    """GET /ready returns 200 when the Ollama probe succeeds."""
    cache, _probe = _make_cache(results=[True])
    app.dependency_overrides[get_probe_cache] = lambda: cache
    try:
        response = await client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# /ready — probe returning False
# ---------------------------------------------------------------------------


async def test_ready_returns_503_when_probe_fails(client: AsyncClient) -> None:
    """GET /ready returns 503 when the Ollama probe fails."""
    cache, _probe = _make_cache(results=[False])
    app.dependency_overrides[get_probe_cache] = lambda: cache
    try:
        response = await client.get("/ready")
        assert response.status_code == 503
        body: dict[str, Any] = response.json()
        assert body["status"] == "not_ready"
        assert body["reason"] == "ollama_unreachable"
    finally:
        app.dependency_overrides.clear()


async def test_ready_503_has_json_content_type(client: AsyncClient) -> None:
    """The 503 response must be structured JSON, not a bare status code."""
    cache, _probe = _make_cache(results=[False])
    app.dependency_overrides[get_probe_cache] = lambda: cache
    try:
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.headers["content-type"] == "application/json"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# /ready — TTL caching
# ---------------------------------------------------------------------------


async def test_ready_caches_probe_within_ttl(client: AsyncClient) -> None:
    """Two /ready calls within the TTL should trigger the probe only once."""
    cache, probe = _make_cache(results=[True], ttl=60.0)
    app.dependency_overrides[get_probe_cache] = lambda: cache
    try:
        await client.get("/ready")
        await client.get("/ready")
        assert probe.call_count == 1
    finally:
        app.dependency_overrides.clear()


async def test_ready_ttl_flip_true_to_false(client: AsyncClient) -> None:
    """Probe flips True→False: cached True is served until TTL expires."""
    cache, probe = _make_cache(results=[True, False], ttl=0.05)
    app.dependency_overrides[get_probe_cache] = lambda: cache
    try:
        # First call: probe returns True → 200
        r1 = await client.get("/ready")
        assert r1.status_code == 200

        # Immediately after: still cached True → 200
        r2 = await client.get("/ready")
        assert r2.status_code == 200
        assert probe.call_count == 1

        # Wait for TTL expiry, then call again: probe returns False → 503
        await asyncio.sleep(0.06)
        r3 = await client.get("/ready")
        assert r3.status_code == 503
        assert probe.call_count == 2
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


async def test_openapi_includes_health_and_ready(client: AsyncClient) -> None:
    """OpenAPI doc must include both /health and /ready operations."""
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    spec: dict[str, Any] = response.json()
    paths = spec.get("paths", {})
    assert "/health" in paths, f"/health missing from OpenAPI paths: {list(paths)}"
    assert "/ready" in paths, f"/ready missing from OpenAPI paths: {list(paths)}"
    assert "get" in paths["/health"]
    assert "get" in paths["/ready"]


async def test_openapi_ready_documents_503(client: AsyncClient) -> None:
    """OpenAPI spec must declare the 503 response for /ready."""
    response = await client.get("/openapi.json")
    spec: dict[str, Any] = response.json()
    ready_responses = spec["paths"]["/ready"]["get"]["responses"]
    assert "503" in ready_responses, (
        f"/ready missing 503 in OpenAPI responses: {list(ready_responses)}"
    )


# ---------------------------------------------------------------------------
# Middleware (preserved from original)
# ---------------------------------------------------------------------------


async def test_response_includes_x_request_id(client: AsyncClient) -> None:
    """Every response should include an X-Request-Id header in 32-char hex format."""
    import re

    response = await client.get("/health")
    assert "X-Request-Id" in response.headers
    assert re.match(r"^[a-f0-9]{32}$", response.headers["X-Request-Id"])


async def test_cors_allows_configured_origin(client: AsyncClient) -> None:
    """CORS should allow the configured origin."""
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_cors_rejects_unconfigured_origin(client: AsyncClient) -> None:
    """CORS should not allow an unconfigured origin."""
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://evil.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") != "http://evil.com"

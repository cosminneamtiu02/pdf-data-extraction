"""Integration tests for health, readiness, middleware, and CORS."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncClient:
    """HTTP client bound to the FastAPI ASGI app in-process (no DB, no network)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health_returns_200(client: AsyncClient) -> None:
    """GET /health should return 200 with status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_returns_200(client: AsyncClient) -> None:
    """GET /ready returns 200 in the minimal shell.

    The Ollama-probe-aware readiness logic lands in feature-dev for PDFX-E007-F001;
    at the post-bootstrap stage the endpoint is a simple stub.
    """
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


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

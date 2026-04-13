"""Integration tests for health, readiness, middleware, and CORS."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session


async def test_health_returns_200(client: AsyncClient):
    """GET /health should return 200 with status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_returns_200_when_db_reachable(client: AsyncClient):
    """GET /ready should return 200 when Postgres is up."""
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_ready_returns_503_when_db_unreachable(client: AsyncClient):
    """GET /ready should return 503 when DB is unreachable."""
    from app.main import app

    broken_session = AsyncMock(spec=AsyncSession)
    broken_session.execute.side_effect = ConnectionRefusedError("simulated DB failure")

    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        yield broken_session

    original_override = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override_get_session
    try:
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not ready"}
    finally:
        if original_override is not None:
            app.dependency_overrides[get_session] = original_override
        else:
            app.dependency_overrides.pop(get_session, None)


async def test_response_includes_x_request_id(client: AsyncClient):
    """Every response should include an X-Request-ID header."""
    response = await client.get("/health")
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 0


async def test_response_includes_security_headers(client: AsyncClient):
    """Every response should include security headers."""
    response = await client.get("/health")
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_cors_allows_configured_origin(client: AsyncClient):
    """CORS should allow the configured origin."""
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_cors_rejects_unconfigured_origin(client: AsyncClient):
    """CORS should not allow an unconfigured origin."""
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://evil.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") != "http://evil.com"

"""Integration tests for health, readiness, middleware, and CORS.

The /ready endpoint is gated on a TTL-cached Ollama probe (PDFX-E007-F001)
*and* on a non-empty skill manifest (issue #108). These tests override
``get_probe_cache`` to inject a controllable ``ProbeCache`` with a
``FakeProbe`` and ``get_skill_manifest`` to inject a non-empty manifest,
so no real Ollama is needed and the default module-level ``app`` (whose
packaged ``apps/backend/skills/`` ships only ``.gitkeep``) does not short-
circuit every test on ``no_skills_loaded``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_probe_cache, get_skill_manifest
from app.api.probe_cache import ProbeCache
from app.features.extraction.skills import SkillManifest
from app.main import app
from tests._support.skill_factory import make_skill
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


def _non_empty_manifest() -> SkillManifest:
    """Manifest with one skill so ``/ready`` does not short-circuit on empty.

    The module-level ``app`` is constructed against the packaged
    ``apps/backend/skills/`` directory which ships only ``.gitkeep``, so
    without this override every ``/ready`` request would return 503 with
    ``no_skills_loaded`` regardless of the probe state under test.
    """
    return SkillManifest({("invoice", 1): make_skill("invoice", 1)})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncClient:
    """HTTP client bound to the FastAPI ASGI app in-process (no DB, no network).

    Installs a non-empty ``SkillManifest`` dependency override for the
    duration of each test so ``/ready`` does not short-circuit on
    ``no_skills_loaded`` (the packaged ``apps/backend/skills/`` ships
    only ``.gitkeep``). Individual tests that install their own probe
    override must tear it down with
    ``app.dependency_overrides.pop(get_probe_cache, None)`` rather than
    ``.clear()`` — the latter would also erase the manifest stand-in
    this fixture owns.
    """
    app.dependency_overrides[get_skill_manifest] = _non_empty_manifest
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_skill_manifest, None)


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
        app.dependency_overrides.pop(get_probe_cache, None)


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
        app.dependency_overrides.pop(get_probe_cache, None)


async def test_ready_503_has_json_content_type(client: AsyncClient) -> None:
    """The 503 response must be structured JSON, not a bare status code."""
    cache, _probe = _make_cache(results=[False])
    app.dependency_overrides[get_probe_cache] = lambda: cache
    try:
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.headers["content-type"] == "application/json"
    finally:
        app.dependency_overrides.pop(get_probe_cache, None)


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
        app.dependency_overrides.pop(get_probe_cache, None)


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
        app.dependency_overrides.pop(get_probe_cache, None)


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

"""Integration test for lifespan-scoped service cache invalidation on shutdown.

Pins the contract that re-entering the ASGI lifespan on a reused FastAPI app
instance rebuilds every cached dependency whose reachable state can hold a
closed ``httpx.AsyncClient``. Before the fix, ``_lifespan``'s shutdown block
only ``delattr``'d ``ollama_health_probe``, ``probe_cache``, and
``intelligence_provider`` — but ``extraction_service`` (which holds the
provider internally) stayed cached. The second lifespan's new provider was
built and stored under ``app.state.intelligence_provider``, but
``get_extraction_service`` still returned the first lifespan's cached
service, whose ``_intelligence_provider`` pointed at the provider that was
``aclose()``'d during the first shutdown. The next extraction call then
blew up with ``httpx.ClosedResourceError``.

This test drives the lifespan twice on the same app, resolves
``get_extraction_service`` inside each lifespan via a probe route (FastAPI's
Depends() graph is what the real ``/api/v1/extract`` route also traverses),
and asserts the second lifespan produces fresh objects — not the stale ones
cached on ``app.state`` from the first lifespan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_extraction_service
from app.core.config import Settings
from app.features.extraction.service import (  # noqa: TC001 — FastAPI reads the runtime annotation on the probe route
    ExtractionService,
)
from app.main import create_app
from tests.conftest import FakeProbe


def _write_valid_skill(base: Path) -> None:
    body = {
        "name": "invoice",
        "version": 1,
        "prompt": "Extract header fields.",
        "examples": [{"input": "INV-1", "output": {"number": "INV-1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
    }
    target = base / "invoice"
    target.mkdir(parents=True, exist_ok=True)
    (target / "1.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _settings_for(skills_dir: Path) -> Settings:
    return Settings(  # type: ignore[reportCallIssue]
        skills_dir=skills_dir,
        app_env="development",
    )


async def test_extraction_service_cache_invalidated_on_shutdown(tmp_path: Path) -> None:
    """Re-entering the lifespan must rebuild the cached ExtractionService.

    Without the shutdown-time ``delattr`` on ``extraction_service``, the
    second lifespan's dependency resolution hands back the first lifespan's
    service — whose internal ``_intelligence_provider`` was ``aclose()``'d
    during the first shutdown. The bug surfaces as a 500 on the next
    extraction request (closed httpx client). We pin the root cause at the
    dependency-resolution layer so the regression is caught without needing
    to assert on transport-level failures.
    """
    _write_valid_skill(tmp_path)
    app = create_app(_settings_for(tmp_path))
    # Deterministic readiness — the lifespan will respect this pre-installed
    # probe and not open any real TCP connections.
    app.state.ollama_health_probe = FakeProbe(results=[False, False])

    captured: list[ExtractionService] = []

    @app.get("/_probe_service")
    async def probe(
        service: Annotated[ExtractionService, Depends(get_extraction_service)],
    ) -> dict[str, bool]:
        captured.append(service)
        return {"ok": True}

    # --- First lifespan ---
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        first = await client.get("/_probe_service")
    assert first.status_code == 200
    service1 = captured[0]
    provider1 = service1._intelligence_provider  # noqa: SLF001 — pinning cache-invalidation contract

    # After the first shutdown, the provider's httpx client is closed.
    assert provider1.http_client.is_closed is True

    # Re-install the fake probe because the first shutdown cleared it.
    app.state.ollama_health_probe = FakeProbe(results=[False, False])

    # --- Second lifespan on the same app ---
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        second = await client.get("/_probe_service")
    assert second.status_code == 200
    service2 = captured[1]
    provider2 = service2._intelligence_provider  # noqa: SLF001 — pinning cache-invalidation contract

    # The service and its intelligence provider must be brand-new instances —
    # otherwise the second lifespan's extraction calls hit a closed client.
    assert service2 is not service1
    assert provider2 is not provider1

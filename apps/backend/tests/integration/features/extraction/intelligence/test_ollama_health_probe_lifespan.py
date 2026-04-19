"""Integration tests for OllamaHealthProbe lifespan cleanup.

Pins the contract that the probe's ``httpx.AsyncClient`` connection pool is
released on ASGI lifespan shutdown. Before this was wired, the probe's
client leaked: uvicorn would log "coroutine was never awaited" warnings on
graceful shutdown and sockets only reclaimed at process exit.

Mirrors ``test_lifespan_shutdown_closes_provider_on_app_state`` in
``test_ollama_gemma_provider_integration.py`` — same shape, same seam
(``app.router.lifespan_context`` because ``httpx.ASGITransport`` does not
fire lifespan events on its own) — but exercises the probe's close path
rather than the provider's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import respx

from app.features.extraction.intelligence.ollama_gemma_provider import (
    build_tags_url,
)
from app.features.extraction.intelligence.ollama_health_probe import (
    OllamaHealthProbe,
)
from app.main import create_app

if TYPE_CHECKING:
    from app.core.config import Settings


@respx.mock
async def test_lifespan_shutdown_closes_probe_http_client() -> None:
    """`_lifespan` must call ``aclose()`` on the probe installed on app.state.

    Builds a real ``OllamaHealthProbe`` (with a real ``httpx.AsyncClient``),
    installs it on ``app.state`` so the lifespan's test-seam branch uses it,
    mocks the ``/api/tags`` endpoint with ``respx`` so the startup probe call
    does not hit the network, then drives the lifespan context manually.

    Asserts the client is open inside the context and closed on exit. Pins
    issue #276: without the lifespan-shutdown ``aclose()`` call, the client
    would stay open and leak its connection pool on process shutdown.
    """
    app = create_app()
    settings: Settings = app.state.settings

    # Mock `/api/tags` so the startup probe call returns a valid response
    # containing the configured model tag. Without this, the real probe would
    # attempt to hit the configured Ollama base URL on startup.
    respx.get(build_tags_url(settings.ollama_base_url)).mock(
        return_value=httpx.Response(200, json={"models": [{"name": settings.ollama_model}]}),
    )

    probe = OllamaHealthProbe(
        tags_url=build_tags_url(settings.ollama_base_url),
        expected_model=settings.ollama_model,
        timeout_seconds=settings.ollama_probe_timeout_seconds,
    )
    app.state.ollama_health_probe = probe

    # Grab a handle to the underlying httpx client before entering lifespan —
    # the lifespan shutdown path ``delattr``s ``ollama_health_probe`` from
    # ``app.state``, so we need our own reference to assert ``is_closed`` on
    # the same client instance after teardown.
    client = probe._http_client  # noqa: SLF001 — pinning client-close contract

    async with app.router.lifespan_context(app):
        assert client.is_closed is False

    assert client.is_closed is True


async def test_probe_aclose_is_idempotent() -> None:
    """Calling ``aclose()`` twice must not raise.

    The lifespan cleanup block calls ``aclose()`` unconditionally on every
    object with that method. If a future refactor reused the same probe
    across two lifespan contexts (or a test harness close-races the cleanup),
    the second call must be a no-op instead of raising. httpx's
    ``AsyncClient.aclose()`` is already idempotent in 0.28.x; this test pins
    that guarantee so a future httpx upgrade or probe refactor does not
    silently regress it.
    """
    probe = OllamaHealthProbe(
        tags_url="http://unused.example/api/tags",
        expected_model="unused",
    )

    await probe.aclose()
    assert probe._http_client.is_closed is True  # noqa: SLF001 — pinning close contract

    # Second call must not raise.
    await probe.aclose()
    assert probe._http_client.is_closed is True  # noqa: SLF001 — pinning close contract

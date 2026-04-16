"""Unit tests for health_router handler logic.

These test the handler return values directly, without the HTTP stack.
Integration tests (in tests/integration/test_health.py) cover the full
ASGI round-trip.
"""

from __future__ import annotations

from app.api.health_router import health, ready

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProbeCache:
    """Minimal ProbeCache stub returning a fixed ``is_ready`` result."""

    def __init__(self, *, ready: bool) -> None:
        self._ready = ready

    async def is_ready(self) -> bool:
        return self._ready


# ---------------------------------------------------------------------------
# /health tests
# ---------------------------------------------------------------------------


async def test_health_returns_ok() -> None:
    result = await health()
    assert result == {"status": "ok"}


# ---------------------------------------------------------------------------
# /ready tests
# ---------------------------------------------------------------------------


async def test_ready_returns_200_when_probe_cache_ready() -> None:
    cache = _FakeProbeCache(ready=True)
    response = await ready(
        probe_cache=cache,  # type: ignore[arg-type]  # test seam
    )
    assert response.status_code == 200
    assert response.body == b'{"status":"ready"}'


async def test_ready_returns_503_when_probe_cache_not_ready() -> None:
    cache = _FakeProbeCache(ready=False)
    response = await ready(
        probe_cache=cache,  # type: ignore[arg-type]  # test seam
    )
    assert response.status_code == 503
    assert b'"not_ready"' in response.body
    assert b'"ollama_unreachable"' in response.body

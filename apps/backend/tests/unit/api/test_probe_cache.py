"""Unit tests for ProbeCache — TTL-cached readiness probe result."""

from __future__ import annotations

import asyncio

from app.api.probe_cache import ProbeCache

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProbe:
    """Records ``check()`` calls and returns scripted results."""

    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.call_count = 0

    async def check(self) -> bool:
        self.call_count += 1
        return self._results[self.call_count - 1]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_first_call_triggers_probe() -> None:
    probe = _FakeProbe(results=[True])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    result = await cache.is_ready()

    assert result is True
    assert probe.call_count == 1


async def test_second_call_within_ttl_returns_cached() -> None:
    probe = _FakeProbe(results=[True])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    await cache.is_ready()
    result = await cache.is_ready()

    assert result is True
    assert probe.call_count == 1


async def test_call_after_ttl_expiry_reprobes() -> None:
    probe = _FakeProbe(results=[True, False])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=0.05,
    )

    first = await cache.is_ready()
    await asyncio.sleep(0.06)
    second = await cache.is_ready()

    assert first is True
    assert second is False
    assert probe.call_count == 2


async def test_stale_true_served_within_ttl_after_probe_flips() -> None:
    probe = _FakeProbe(results=[True, False])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    first = await cache.is_ready()
    # Probe would now return False, but TTL hasn't expired
    second = await cache.is_ready()

    assert first is True
    assert second is True  # stale cached result
    assert probe.call_count == 1


async def test_stale_flips_after_ttl_expires() -> None:
    probe = _FakeProbe(results=[True, False])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=0.05,
    )

    first = await cache.is_ready()
    assert first is True

    await asyncio.sleep(0.06)
    second = await cache.is_ready()

    assert second is False
    assert probe.call_count == 2


async def test_zero_ttl_always_reprobes() -> None:
    probe = _FakeProbe(results=[True, True])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=0.0,
    )

    await cache.is_ready()
    await cache.is_ready()

    assert probe.call_count == 2


async def test_settings_default_ttl() -> None:
    from app.core.config import Settings

    settings = Settings(
        skills_dir="/tmp/fake",  # noqa: S108 - test fixture path
    )
    assert settings.ollama_probe_ttl_seconds == 10.0

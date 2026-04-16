"""Unit tests for ProbeCache.prime() and recovery logging (PDFX-E007-F002)."""

from __future__ import annotations

import asyncio

from structlog.testing import capture_logs

from app.api.probe_cache import ProbeCache
from tests.conftest import FakeProbe


async def test_prime_false_returns_false_within_ttl() -> None:
    """Primed with False → is_ready() returns False without calling probe."""
    probe = FakeProbe(results=[])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    cache.prime(result=False)
    result = await cache.is_ready()

    assert result is False
    assert probe.call_count == 0


async def test_prime_true_returns_true_within_ttl() -> None:
    """Primed with True → is_ready() returns True without calling probe."""
    probe = FakeProbe(results=[])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    cache.prime(result=True)
    result = await cache.is_ready()

    assert result is True
    assert probe.call_count == 0


async def test_recovery_logged_when_prime_false_then_probe_true() -> None:
    """Primed False → TTL expires → probe True → logs ollama_reachable_recovered."""
    probe = FakeProbe(results=[True])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=0.05,
    )

    cache.prime(result=False)
    await asyncio.sleep(0.06)

    with capture_logs() as cap_logs:
        result = await cache.is_ready()

    assert result is True
    assert probe.call_count == 1
    recovery_events = [e for e in cap_logs if e["event"] == "ollama_reachable_recovered"]
    assert len(recovery_events) == 1
    assert recovery_events[0]["log_level"] == "info"


async def test_no_recovery_logged_when_prime_true_then_probe_true() -> None:
    """Primed True → TTL expires → probe True → no recovery event."""
    probe = FakeProbe(results=[True])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=0.05,
    )

    cache.prime(result=True)
    await asyncio.sleep(0.06)

    with capture_logs() as cap_logs:
        result = await cache.is_ready()

    assert result is True
    recovery_events = [e for e in cap_logs if e["event"] == "ollama_reachable_recovered"]
    assert len(recovery_events) == 0


async def test_no_recovery_logged_when_prime_true_then_probe_false() -> None:
    """Primed True → TTL expires → probe False → no recovery event (degradation)."""
    probe = FakeProbe(results=[False])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=0.05,
    )

    cache.prime(result=True)
    await asyncio.sleep(0.06)

    with capture_logs() as cap_logs:
        result = await cache.is_ready()

    assert result is False
    recovery_events = [e for e in cap_logs if e["event"] == "ollama_reachable_recovered"]
    assert len(recovery_events) == 0


async def test_no_recovery_logged_on_first_call_without_prime() -> None:
    """Never primed, probe returns False → no recovery event (initial failure)."""
    probe = FakeProbe(results=[False])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    with capture_logs() as cap_logs:
        result = await cache.is_ready()

    assert result is False
    recovery_events = [e for e in cap_logs if e["event"] == "ollama_reachable_recovered"]
    assert len(recovery_events) == 0

"""Unit tests for ProbeCache — TTL-cached readiness probe result."""

from __future__ import annotations

import asyncio

from structlog.testing import capture_logs

from app.api.probe_cache import ProbeCache
from tests.conftest import FakeProbe


class _ExplodingProbe:
    """Probe stub whose ``check()`` raises a configured exception every call.

    Simulates the class of failure described in issue #144 — any exception
    that is not an ``httpx.HTTPError`` the real probe already catches (e.g.
    ``ValueError`` from a bad config, ``AttributeError`` on a wrapped
    client, a ``DomainError`` subclass from the probe path). The cache
    must convert this into cached-``False`` so ``/ready`` keeps returning
    503 ``ollama_unreachable`` instead of 500.
    """

    def __init__(self, *, error: Exception) -> None:
        self._error = error
        self.call_count = 0

    async def check(self) -> bool:
        self.call_count += 1
        raise self._error


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_first_call_triggers_probe() -> None:
    probe = FakeProbe(results=[True])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    result = await cache.is_ready()

    assert result is True
    assert probe.call_count == 1


async def test_second_call_within_ttl_returns_cached() -> None:
    probe = FakeProbe(results=[True])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    await cache.is_ready()
    result = await cache.is_ready()

    assert result is True
    assert probe.call_count == 1


async def test_call_after_ttl_expiry_reprobes() -> None:
    probe = FakeProbe(results=[True, False])
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
    probe = FakeProbe(results=[True, False])
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
    probe = FakeProbe(results=[True, False])
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
    probe = FakeProbe(results=[True, True])
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=0.0,
    )

    await cache.is_ready()
    await cache.is_ready()

    assert probe.call_count == 2


# ---------------------------------------------------------------------------
# Tests — non-httpx exceptions from probe.check() must not escape the cache
# ---------------------------------------------------------------------------
#
# The lifespan guard for issue #144 keeps startup alive, but once the primed
# TTL window expires ``/ready`` triggers a fresh ``probe.check()`` through
# ``ProbeCache.is_ready()``. If the underlying failure is persistent (bad
# config, wrapped-client attribute error, ...), the refresh path must treat
# the exception exactly like the startup path does: log WARNING, cache
# ``False``, and return ``False`` so ``/ready`` stays on the documented
# 503 ``ollama_unreachable`` contract rather than leaking a 500.


async def test_probe_exception_on_first_call_returns_false() -> None:
    """Unprimed cache + probe raises → is_ready() returns False, not exception."""
    probe = _ExplodingProbe(error=ValueError("simulated"))
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    result = await cache.is_ready()

    assert result is False
    assert probe.call_count == 1


async def test_probe_exception_after_ttl_returns_false_not_raises() -> None:
    """Primed True → TTL expires → probe raises → cache returns False, no raise."""
    probe = _ExplodingProbe(error=ValueError("simulated"))
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=0.05,
    )

    cache.prime(result=True)
    await asyncio.sleep(0.06)

    result = await cache.is_ready()

    assert result is False
    assert probe.call_count == 1


async def test_probe_exception_logs_warning_with_error_class() -> None:
    """Probe exception emits ``probe_check_failed_on_refresh`` WARNING.

    The event name is distinct from the lifespan's
    ``probe_check_failed_at_startup`` so operators tailing logs can
    separate "exploded during boot" from "exploded during runtime
    refresh", and the error class name is included for triage.
    """
    probe = _ExplodingProbe(error=ValueError("simulated"))
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    with capture_logs() as cap_logs:
        await cache.is_ready()

    events = [e for e in cap_logs if e["event"] == "probe_check_failed_on_refresh"]
    assert len(events) == 1
    assert events[0]["log_level"] == "warning"
    assert events[0]["error_class"] == "ValueError"


async def test_probe_exception_caches_false_within_ttl() -> None:
    """After exception → result is cached as False within TTL (no re-probe)."""
    probe = _ExplodingProbe(error=ValueError("simulated"))
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    first = await cache.is_ready()
    second = await cache.is_ready()

    assert first is False
    assert second is False
    # Second call must hit the TTL fast-path rather than re-probing,
    # otherwise a persistent bad-config scenario turns into a thundering
    # herd of exceptions on every /ready hit.
    assert probe.call_count == 1


async def test_probe_exception_to_success_logs_recovery() -> None:
    """Primed True → probe raises (cached False) → probe returns True → recovery event.

    Exercises the transition path: the exception-caught ``False`` must be
    indistinguishable from a normal ``False`` result so the existing
    self-heal logging still fires when the underlying fault clears.
    """
    # First post-prime refresh raises, second returns True.
    recovering_probe = _TransientExplodingProbe(
        error=ValueError("simulated"),
        results=[True],
    )
    cache = ProbeCache(
        probe=recovering_probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=0.05,
    )

    cache.prime(result=True)
    # Degrade path: primed True → TTL expires → probe raises → cache goes False.
    await asyncio.sleep(0.06)
    with capture_logs() as degrade_logs:
        degraded = await cache.is_ready()
    assert degraded is False
    degradation_events = [e for e in degrade_logs if e["event"] == "ollama_became_unreachable"]
    assert len(degradation_events) == 1

    # Recovery path: TTL expires → probe returns True → recovery event fires.
    await asyncio.sleep(0.06)
    with capture_logs() as recover_logs:
        recovered = await cache.is_ready()
    assert recovered is True
    recovery_events = [e for e in recover_logs if e["event"] == "ollama_reachable_recovered"]
    assert len(recovery_events) == 1


class _TransientExplodingProbe:
    """Probe that raises once, then returns scripted results. Used for the
    exception-to-success self-heal test.
    """

    def __init__(self, *, error: Exception, results: list[bool]) -> None:
        self._error = error
        self._results = list(results)
        self.call_count = 0
        self._raised = False

    async def check(self) -> bool:
        self.call_count += 1
        if not self._raised:
            self._raised = True
            raise self._error
        return self._results.pop(0)

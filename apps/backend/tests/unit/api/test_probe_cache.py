"""Unit tests for ProbeCache — TTL-cached readiness probe result."""

from __future__ import annotations

import asyncio

from structlog.testing import capture_logs

from app.api.probe_cache import ProbeCache
from tests.conftest import FakeProbe


class _ExplodingProbe:
    """Probe stub whose ``check()`` raises a configured exception every call.

    Simulates unexpected exceptions *escaping* ``probe.check()`` — the
    failure mode described in issue #144. This is explicitly distinct from
    the ``httpx`` and JSON-decode paths the real ``OllamaHealthProbe``
    already catches internally and converts to ``False``. Examples here
    include ``ValueError`` from a bad config, ``AttributeError`` on a
    wrapped client, ``OSError`` from a lower-level transport layer, or a
    ``DomainError`` subclass from the probe path. The cache must convert
    these escaped exceptions into cached-``False`` so ``/ready`` keeps
    returning 503 ``ollama_unreachable`` instead of 500.
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


# ---------------------------------------------------------------------------
# Tests — lock-based coalescing of concurrent refreshes (issue #154)
#
# The ``asyncio.Lock`` inside ``ProbeCache.is_ready()`` is there so N concurrent
# callers that all see an expired cache entry do not each trigger a separate
# ``probe.check()``. One caller owns the lock and does the refresh; the others
# block, then read the just-refreshed cache via the double-check inside the
# critical section. A future refactor that drops the lock, swaps it for a
# non-coalescing primitive, or forgets the inside-the-lock double-check would
# silently regress this invariant — these tests lock it in.
# ---------------------------------------------------------------------------


class _GatedProbe:
    """Probe whose ``check()`` blocks on an ``asyncio.Event`` before returning.

    The gate lets the test hold every concurrent caller inside the refresh path
    simultaneously. Without it, the first caller would typically finish the
    probe synchronously and populate the cache before any other task even
    reaches the lock — defeating the point of the concurrency assertion.
    """

    def __init__(self, *, gate: asyncio.Event, result: bool = True) -> None:
        self._gate = gate
        self._result = result
        self.call_count = 0

    async def check(self) -> bool:
        self.call_count += 1
        # Yield until the test releases the gate so all N tasks pile up on the
        # lock together.
        await self._gate.wait()
        return self._result


async def test_concurrent_is_ready_after_ttl_expiry_coalesces_into_single_probe() -> None:
    """N concurrent callers against an expired cache → exactly one probe call.

    This is the load-bearing contract of the ``asyncio.Lock`` inside
    ``ProbeCache.is_ready()``: a thundering herd of ``/ready`` requests at
    TTL expiry must not produce N simultaneous Ollama probes. Only the first
    coroutine to acquire the lock should call ``probe.check()``; the others
    must block on the lock and then hit the inside-the-lock double-check,
    which sees ``_has_previous_result`` is now True and the last check is
    within the TTL window — so they return the cached value instead of
    re-probing.

    Uses a non-zero TTL so the inside-the-lock double-check actually covers
    the follow-on callers. A TTL of ``0.0`` would not coalesce at all: every
    caller would pass the double-check and re-probe. The pre-refresh "expired"
    state is instead modeled by starting from a freshly-constructed cache
    (``_has_previous_result = False``) so all N tasks pile up on the lock,
    and the first one's refresh is what flips the double-check inside the
    lock from "probe again" to "return cached".
    """
    gate = asyncio.Event()
    probe = _GatedProbe(gate=gate, result=True)
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    # Spawn N concurrent callers. At this point the first one has (or is about
    # to acquire) the lock and is awaiting ``gate``; the other 19 are blocked
    # on ``self._lock``. If coalescing is broken, multiple of them reach
    # ``probe.check()`` and bump ``call_count`` past 1.
    tasks = [asyncio.create_task(cache.is_ready()) for _ in range(20)]

    # Give the event loop enough rounds to schedule every task so they all
    # arrive at the lock before the refresher finishes. Multiple ``sleep(0)``
    # calls flush any queued callbacks in both directions (lock acquisition,
    # ``Event.wait`` setup). A single yield is sometimes not enough on CPython
    # when the event-loop policy batches scheduling.
    for _ in range(5):
        await asyncio.sleep(0)

    gate.set()
    results = await asyncio.gather(*tasks)

    assert all(results), f"expected every caller to see True, got {results}"
    assert probe.call_count == 1, (
        f"expected exactly one probe.check() call under concurrent load, got {probe.call_count}"
    )


async def test_blocked_caller_observes_refresher_result_not_stale_or_reprobed() -> None:
    """Second caller blocked on the lock returns the value the refresher just wrote.

    Verifies the inside-the-lock double-check works as intended: after the
    refresher finishes writing a fresh result, the coroutine that was waiting
    on the lock must return that *fresh* value (not re-probe, not see
    pre-refresh state). This is what makes coalescing a correctness property
    rather than just a performance optimisation — if the blocked caller went
    on to re-probe after acquiring the lock, concurrent ``/ready`` bursts
    would still thunder.
    """
    gate = asyncio.Event()
    probe = _GatedProbe(gate=gate, result=True)
    cache = ProbeCache(
        probe=probe,  # type: ignore[arg-type]  # test seam
        ttl_seconds=5.0,
    )

    refresher_task = asyncio.create_task(cache.is_ready())
    # Let the refresher acquire the lock and start awaiting the gate.
    for _ in range(3):
        await asyncio.sleep(0)
    # Second caller arrives while refresher still holds the lock → it blocks
    # on ``self._lock``. If the inside-the-lock double-check is broken, it
    # would reach ``probe.check()`` after the refresher releases the lock
    # and bump ``call_count``.
    blocked_task = asyncio.create_task(cache.is_ready())
    for _ in range(3):
        await asyncio.sleep(0)

    # Release the refresher; it populates the cache and returns True.
    gate.set()
    refresher_result = await refresher_task
    blocked_result = await blocked_task

    assert refresher_result is True
    assert blocked_result is True, (
        "blocked caller should observe the refresher's result (True), not "
        "trigger its own probe or return stale state"
    )
    assert probe.call_count == 1, (
        f"blocked caller should read the cache inside the lock, "
        f"not re-probe; got {probe.call_count} probe calls"
    )

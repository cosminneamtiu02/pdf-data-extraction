"""Hygiene test: FakeProbe exhaustion propagates through production guards.

Issue #396 / Copilot review on PR #483 round 2.

Both ``ProbeCache.is_ready()`` and ``app.main._lifespan`` wrap
``await probe.check()`` in a broad ``except Exception`` so that any
unexpected ``Exception`` subclass degrades into a cached ``False``
instead of escaping and turning ``/ready`` into a 500. That guard is
load-bearing for the ``/ready`` contract across the full process
lifetime (issue #144, ``probe_check_failed_on_refresh`` WARNING).

It is *also* load-bearing that a misconfigured test — one where
``FakeProbe`` is scripted with too few results for the code under test —
fails loudly rather than silently being converted into a cached ``False``.
A plain ``AssertionError`` from ``FakeProbe.check()`` would be caught by
``except Exception`` and turned into ``False``, and the misconfiguration
would ship as green.

The fix: ``FakeProbe`` raises ``FakeProbeExhausted``, a ``BaseException``
subclass that ``except Exception`` does not match (same reason
``asyncio.CancelledError`` isn't caught by those guards on Python 3.8+).

This test pins the no-silent-swallow invariant on the actual production
code path. If ``FakeProbe`` ever regresses to raise an ``Exception``
subclass (``AssertionError``, ``RuntimeError``, ``pytest.fail``, ...),
``ProbeCache.is_ready()`` will silently return ``False`` and this test
will fail with a clear message.
"""

from __future__ import annotations

import pytest

from app.api.probe_cache import ProbeCache
from tests.conftest import FakeProbe, FakeProbeExhausted


async def test_probecache_does_not_swallow_fakeprobe_exhaustion() -> None:
    # Empty script — the first ``is_ready()`` refresh call exhausts the probe
    # on the very first ``check()`` invocation.
    probe = FakeProbe(results=[])
    cache = ProbeCache(probe=probe, ttl_seconds=60.0)  # type: ignore[arg-type]  # test seam

    # ``ProbeCache.is_ready()`` wraps the probe call in ``except Exception``.
    # If ``FakeProbe`` raises a plain ``Exception`` subclass (e.g. the old
    # ``AssertionError`` behaviour), that guard silently converts it to
    # cached ``False`` and ``is_ready()`` returns ``False`` with no exception
    # propagating. The ``pytest.raises`` block would then fail with
    # "DID NOT RAISE", catching the regression.
    with pytest.raises(FakeProbeExhausted) as excinfo:
        await cache.is_ready()

    assert excinfo.type is FakeProbeExhausted
    # The sentinel must remain outside the ``Exception`` hierarchy, otherwise
    # the except-Exception guard in ``ProbeCache.is_ready()`` would match it
    # and we would not have reached this ``pytest.raises`` block at all.
    assert not isinstance(excinfo.value, Exception)
    assert "FakeProbe.check called more times than scripted" in str(excinfo.value)


async def test_probecache_still_degrades_real_exception_subclasses() -> None:
    """Complement to the test above: ``except Exception`` must still degrade
    genuine ``Exception`` subclasses. Without this invariant, the ``/ready``
    500-vs-503 contract (issue #144) silently regresses."""

    class _BadProbe:
        async def check(self) -> bool:  # pragma: no cover - raises unconditionally
            msg = "simulated production fault"
            raise RuntimeError(msg)

    cache = ProbeCache(probe=_BadProbe(), ttl_seconds=60.0)  # type: ignore[arg-type]  # test seam
    # Must NOT raise — the production guard converts RuntimeError into a
    # cached ``False`` so ``/ready`` stays on the documented 503 contract.
    result = await cache.is_ready()
    assert result is False

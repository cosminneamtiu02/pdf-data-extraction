"""Shared test fixtures and fakes used across unit and integration tests.

Kept deliberately skinny -- this module loads for every test file pytest
collects, so any import here is a tax paid by the entire suite (issue
#354). Helpers whose construction needs production-module imports live
under ``tests/_support/`` and are imported explicitly by the test
modules that actually use them.
"""

from __future__ import annotations


class FakeProbeExhausted(BaseException):
    """Raised by ``FakeProbe.check`` when scripted results are exhausted.

    Subclasses ``BaseException`` -- *not* ``Exception`` -- deliberately.

    Production code paths that consume ``probe.check()`` (``ProbeCache.is_ready``
    and ``app.main._lifespan``) wrap the call in a broad ``except Exception``
    so that any unexpected ``Exception`` subclass is degraded into a cached
    ``False`` instead of propagating and turning ``/ready`` into a 500 (issue
    #144). That guard is load-bearing for the ``/ready`` contract -- but it
    would silently swallow an ``AssertionError`` from an exhausted ``FakeProbe``,
    converting a misconfigured test into a green ``False`` result instead of a
    loud pytest failure.

    Making the exhaustion signal a ``BaseException`` subclass means ``except
    Exception`` does not match it (same reason ``asyncio.CancelledError`` isn't
    caught by those guards on Python 3.8+), so the signal propagates all the
    way up to pytest and the test fails at the offending ``await`` site with a
    readable stack. (Issues #396 and the Copilot review on PR #483.)
    """


class FakeProbe:
    """Controllable probe returning scripted boolean results.

    Used by both unit tests (``test_probe_cache``) and integration tests
    (``test_health``) to stub ``OllamaHealthProbe.check()`` without a
    real Ollama instance.
    """

    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.call_count = 0

    async def check(self) -> bool:
        if self.call_count >= len(self._results):
            # Raise ``FakeProbeExhausted`` (a ``BaseException`` subclass) rather
            # than ``AssertionError`` or ``pytest.fail``: production code wraps
            # ``probe.check()`` in ``except Exception`` to degrade rather than
            # crash on unexpected failures (issue #144). An ``AssertionError``
            # would be caught by that guard and silently converted into a
            # cached ``False``, turning a misconfigured test into a green
            # result. ``FakeProbeExhausted`` bypasses ``except Exception`` the
            # same way ``asyncio.CancelledError`` does, so the exhaustion
            # signal surfaces at the offending ``await`` site. (Issue #396,
            # Copilot review on PR #483.)
            msg = f"FakeProbe.check called more times than scripted (call #{self.call_count + 1})"
            raise FakeProbeExhausted(msg)
        result = self._results[self.call_count]
        self.call_count += 1
        return result

"""Hygiene test: ``FakeProbe.check`` raises a ``BaseException`` sentinel on exhaustion.

Issue #396 (round 1): the shared ``FakeProbe`` in ``apps/backend/tests/conftest.py``
originally called ``pytest.fail(...)`` when scripted results were exhausted.
Under pytest-asyncio's session-scoped event loop, the resulting ``Failed``
report surfaced on the async test that happened to consume the probe, not
on the logically-wrong call site — the stack became opaque.

Copilot review on PR #483 (round 2): switching to a plain ``AssertionError``
fixed the stack-propagation issue but left a silent-swallow bug in place.
Production code wraps ``probe.check()`` in a broad ``except Exception``
inside ``ProbeCache.is_ready()`` and ``app.main._lifespan`` so that any
unexpected ``Exception`` subclass degrades into a cached ``False`` instead
of escaping and turning ``/ready`` into a 500 (issue #144). ``AssertionError``
inherits from ``Exception``, so an exhausted ``FakeProbe`` inside a test
that went through those guards would be converted into ``False`` and the
misconfiguration would ship as green.

The current contract — pinned here — is: ``FakeProbe`` raises
``FakeProbeExhausted``, a ``BaseException`` subclass. ``except Exception``
does not match it, the signal propagates, and the test fails loudly with
the stack at the offending ``await`` site. A sibling meta test
(``test_fakeprobe_exhaustion_propagates_through_probecache``) pins the
no-silent-swallow invariant on the actual production path so that both
halves of the contract are locked in.

The filename preserves "raises_assertion_error" for git-blame continuity
with the original #396 fix; the assertion below now pins the stricter
``BaseException``-sentinel contract.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import FakeProbe, FakeProbeExhausted


def test_fakeprobe_check_raises_exhaustion_sentinel_when_exhausted() -> None:
    probe = FakeProbe(results=[True])

    # First call consumes the only scripted result — must succeed.
    first = asyncio.run(probe.check())
    assert first is True

    # Second call exhausts the script. Must raise ``FakeProbeExhausted``
    # (NOT ``AssertionError``, NOT ``pytest.outcomes.Failed``) so the
    # signal propagates through production ``except Exception`` guards.
    with pytest.raises(FakeProbeExhausted) as excinfo:
        asyncio.run(probe.check())

    # Guard against silent regression to either of the older failure modes:
    #   - ``pytest.fail`` → ``Failed`` (opaque stack under pytest-asyncio)
    #   - plain ``AssertionError`` (swallowed by ``except Exception``)
    # ``excinfo.type is FakeProbeExhausted`` is preferred over
    # ``type(excinfo.value) is FakeProbeExhausted`` per ruff E721.
    assert excinfo.type is FakeProbeExhausted, (
        f"Expected FakeProbeExhausted, got {excinfo.type.__name__}. "
        "If this is AssertionError or pytest.outcomes.Failed, the #483 "
        "review fix has regressed — FakeProbe is no longer a BaseException-only "
        "signal and ProbeCache's except-Exception guard will silently swallow "
        "exhaustion again."
    )
    # The sentinel must remain outside the ``Exception`` hierarchy so that
    # production ``except Exception`` blocks do not catch it. This is the
    # load-bearing invariant the rename to ``BaseException`` was for.
    assert not isinstance(excinfo.value, Exception), (
        "FakeProbeExhausted must NOT be an Exception subclass; otherwise "
        "ProbeCache.is_ready()'s except-Exception guard will silently swallow "
        "exhaustion and convert a misconfigured test into a cached False."
    )
    assert "FakeProbe.check called more times than scripted" in str(excinfo.value)

"""Hygiene test: ``FakeProbe.check`` must raise ``AssertionError`` on exhaustion.

Issue #396: the shared ``FakeProbe`` in ``apps/backend/tests/conftest.py``
originally called ``pytest.fail(...)`` when scripted results were exhausted.
Under pytest-asyncio's session-scoped event loop, the resulting ``Failed``
report surfaces on the async test that happened to consume the probe, not
on the logically-wrong call site — the stack becomes opaque.

Raising a plain ``AssertionError`` instead makes pytest report the stack
at the offending ``await`` site, which is what you want when diagnosing a
misconfigured probe.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import FakeProbe


def test_fakeprobe_check_raises_assertion_error_when_exhausted() -> None:
    probe = FakeProbe(results=[True])

    # First call consumes the only scripted result — must succeed.
    first = asyncio.run(probe.check())
    assert first is True

    # Second call exhausts the script. Must raise AssertionError (NOT
    # pytest.outcomes.Failed) so the stack surfaces at the caller under
    # pytest-asyncio's session-scoped loop propagation.
    with pytest.raises(AssertionError) as excinfo:
        asyncio.run(probe.check())

    # Guard against silent regression to ``pytest.fail``, which raises a
    # ``Failed`` exception that is NOT a subclass of ``AssertionError``.
    assert type(excinfo.value) is AssertionError, (
        f"Expected plain AssertionError, got {type(excinfo.value).__name__}. "
        "If this is pytest.outcomes.Failed, #396 has regressed — FakeProbe "
        "is calling pytest.fail(...) again."
    )
    assert "FakeProbe.check called more times than scripted" in str(excinfo.value)

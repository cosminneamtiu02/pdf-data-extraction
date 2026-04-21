"""Meta-test for ``_select_profile`` env-var validation (issue #353 follow-up).

Copilot review on PR #458 flagged that the contract conftest's
``_select_profile()`` helper handed the raw ``HYPOTHESIS_PROFILE``
environment variable straight to ``hypothesis_settings.load_profile``
without any allow-list check. A typoed value (``HYPOTHESIS_PROFILE=cii``)
would fail at import time with Hypothesis' generic ``InvalidArgument``
stack trace, which is hard to diagnose from bare pytest collection
output.

These tests pin the friendlier behaviour:

1. ``_select_profile()`` rejects an unregistered env-var value with a
   ``ValueError`` that names the offender AND lists the allowed
   profile names, so the failure message tells the caller exactly what
   to fix.
2. ``_select_profile()`` still accepts the two registered profile
   names (``ci`` and ``dev``) without raising.
3. When the env var is unset, ``_select_profile()`` returns the safe
   ``ci`` default.

Importing ``tests.contract.conftest`` from a unit test is a deliberate
choice here: ``_select_profile`` is private to that module, and the
module's side effects (two ``register_profile`` calls and one
``load_profile`` call for ``ci``) are idempotent and don't leak into
other unit tests — Hypothesis isn't exercised by the unit suite.
"""

from __future__ import annotations

import pytest


def test_select_profile_returns_ci_default_when_env_var_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ``HYPOTHESIS_PROFILE`` must resolve to the safe ``ci`` default."""
    from tests.contract.conftest import _select_profile

    monkeypatch.delenv("HYPOTHESIS_PROFILE", raising=False)

    assert _select_profile() == "ci"


@pytest.mark.parametrize("profile_name", ["ci", "dev"])
def test_select_profile_accepts_registered_profile_names(
    monkeypatch: pytest.MonkeyPatch,
    profile_name: str,
) -> None:
    """``HYPOTHESIS_PROFILE`` set to a registered name passes through unchanged."""
    from tests.contract.conftest import _select_profile

    monkeypatch.setenv("HYPOTHESIS_PROFILE", profile_name)

    assert _select_profile() == profile_name


def test_select_profile_rejects_unknown_env_var_with_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typoed ``HYPOTHESIS_PROFILE`` must raise ``ValueError`` listing allowed names.

    Without the allow-list guard, ``hypothesis_settings.load_profile("cii")``
    would raise ``InvalidArgument`` at conftest import time with a terse
    Hypothesis-library message. The wrapper guard replaces that with a
    ``ValueError`` whose message names the offending value AND every
    registered profile, so bare ``--collect-only`` output tells the
    caller both what broke and how to fix it.
    """
    from tests.contract.conftest import _select_profile

    bogus = "cii"
    monkeypatch.setenv("HYPOTHESIS_PROFILE", bogus)

    # `match=bogus` pins the offending value into the message; the
    # remaining per-component assertions below pin the allow-list names
    # and the env-var name so a regression that keeps the exception
    # type but drops guidance still fails this test.
    with pytest.raises(ValueError, match=bogus) as exc_info:
        _select_profile()

    message = str(exc_info.value)
    assert "ci" in message, "error message must list 'ci' as an allowed profile"
    assert "dev" in message, "error message must list 'dev' as an allowed profile"
    assert "HYPOTHESIS_PROFILE" in message, (
        "error message must identify the env var so the caller knows where to look"
    )

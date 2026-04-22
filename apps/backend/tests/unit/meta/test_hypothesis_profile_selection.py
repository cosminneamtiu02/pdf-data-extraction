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
choice here: ``_select_profile`` is private to that module. The module
import is now side-effect-free with respect to Hypothesis' active
profile — ``load_profile`` is deferred to a ``pytest_configure`` hook,
so importing the conftest from the unit suite registers the ``ci`` and
``dev`` profiles (idempotent) but does not mutate which profile is
active. That means these unit tests no longer need a module-scope
``os.environ.pop`` to protect conftest import from a typoed
``HYPOTHESIS_PROFILE`` in the developer's shell — ``_select_profile``
is a pure function now invoked only from inside each test under
``monkeypatch`` control.
"""

from __future__ import annotations

import pytest
from hypothesis import settings as hypothesis_settings


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
    would raise ``InvalidArgument`` when the ``pytest_configure`` hook
    called it, with a terse Hypothesis-library message. The wrapper
    guard replaces that with a ``ValueError`` whose message names the
    offending value AND every registered profile, so the pytest startup
    error tells the caller both what broke and how to fix it.
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


def test_default_profile_is_still_active_detects_mismatched_fingerprint() -> None:
    """``_default_profile_is_still_active`` must return ``False`` while a non-default profile is loaded.

    The helper's one job is to be a reliable fingerprint probe: when a
    non-default profile (e.g. ``ci``) is the active one, the fingerprint
    must NOT match Hypothesis' built-in default. The unit suite now
    exercises that invariant by loading ``ci`` explicitly inside the
    test body and restoring the previously-active profile on teardown.
    The contract conftest import no longer triggers ``load_profile`` as
    a side effect (the load is deferred to a ``pytest_configure`` hook
    the unit suite never runs), so without the in-test load step this
    test would only ever see Hypothesis' default profile.

    ``try/finally`` restores whatever profile was active when the test
    started, so a failure here does not leak ``ci`` into the rest of
    the unit suite and flake unrelated property-based tests.
    """
    # Importing the contract conftest registers ``ci`` and ``dev``
    # idempotently; ``load_profile`` below would raise if ``ci`` were
    # not registered.
    from tests.contract.conftest import _default_profile_is_still_active

    previous_profile = hypothesis_settings.get_current_profile_name()
    hypothesis_settings.load_profile("ci")
    try:
        assert _default_profile_is_still_active() is False, (
            "_default_profile_is_still_active() returned True while the "
            "'ci' profile was the active one. The fingerprint probe is "
            "broken — see apps/backend/tests/contract/conftest.py "
            "_default_profile_is_still_active. Issue #353."
        )
    finally:
        hypothesis_settings.load_profile(previous_profile)

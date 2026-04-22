"""Meta-test: contract-test conftest must register Hypothesis settings profiles.

Why this guardrail exists (issue #353)
--------------------------------------
Today every test in ``apps/backend/tests/contract/test_schemathesis.py``
is a hand-rolled one-shot request + ``schema[...].validate_response``
check — no ``@schema.parametrize`` decorator, no stateful Hypothesis
strategy. That means the Hypothesis plugin currently contributes zero
examples per run, so the library's defaults (``max_examples=100``,
``deadline=200ms``) never fire.

The moment a future PR adds ``@schema.parametrize`` for a simpler
endpoint (``/health`` is the obvious next target), the schemathesis/
Hypothesis stack silently inherits those defaults. A
``deadline=200ms`` limit will flake on CI's noisier runner even for a
trivial JSON endpoint, and ``max_examples=100`` per operation will
blow the contract-test budget (``task test:contract`` has a 300 s
timeout). Fixing it after it flakes means chasing retry loops; pinning
a registered profile up front means the first PR that adds
``@schema.parametrize`` lands with a known, bounded, deterministic
budget.

What this test pins
-------------------
1. The contract ``conftest.py`` registers at least the ``ci`` profile
   (low ``max_examples``, generous ``deadline``, deterministic seed via
   ``derandomize=True``) and the ``dev`` profile (larger
   ``max_examples`` for local exploration).
2. One of those profiles is loaded at test-collection time, so future
   ``@schema.parametrize`` decorators run under an explicit budget
   rather than Hypothesis defaults.
3. The loaded profile is selectable via either the
   ``HYPOTHESIS_PROFILE`` environment variable or pytest's
   ``--hypothesis-profile=<name>`` flag (the standard Hypothesis
   integration surface — see
   https://hypothesis.readthedocs.io/en/latest/reference/integrations.html).

When this test fails, either ``conftest.py`` dropped its
``register_profile`` call (re-add it) or a sibling PR renamed the
profile without updating the allow-list below (keep the name and the
test in sync).
"""

from __future__ import annotations

from hypothesis import settings as hypothesis_settings

_REQUIRED_PROFILES = ("ci", "dev")
_CI_MAX_EXAMPLES_CEILING = 100  # Hypothesis default; CI profile must stay at or below it.
_CI_DEADLINE_FLOOR_MS = 1000  # CI profile deadline must exceed Hypothesis' 200 ms default.


def test_ci_and_dev_profiles_are_registered() -> None:
    """``settings.get_profile`` must succeed for every profile we pin."""
    for name in _REQUIRED_PROFILES:
        # `get_profile` raises `InvalidArgument` if the profile was never
        # registered. Letting it raise surfaces the exact missing name in
        # the pytest failure report, which is what we want.
        profile = hypothesis_settings.get_profile(name)
        assert profile is not None, (
            f"Hypothesis profile {name!r} returned None from get_profile; "
            f"re-register it in apps/backend/tests/contract/conftest.py (issue #353)."
        )


def test_ci_profile_budget_is_bounded() -> None:
    """The ``ci`` profile must cap ``max_examples`` and raise ``deadline``.

    Rationale: Hypothesis' default ``max_examples=100`` + ``deadline=200ms``
    will flake on CI's noisier runner. The ``ci`` profile exists precisely
    to tighten the example budget (so contract tests stay within the 300 s
    timeout declared on ``task test:contract``) AND loosen the deadline
    (so a slow CI runner doesn't fail an otherwise-valid example).

    The deadline must be an actual positive duration, not ``None``.
    ``deadline=None`` disables the per-example time limit entirely, which
    is the opposite of "raise it" — a Hypothesis strategy that gets
    pathologically slow (e.g. an unbounded text generator) would then
    consume the entire 300 s contract-test budget before the runner
    flagged anything. Requiring a concrete floor makes the invariant
    match the docstring and catches a regression where somebody "fixes"
    a flake by turning the deadline off entirely instead of raising it.
    """
    ci_profile = hypothesis_settings.get_profile("ci")

    assert ci_profile.max_examples <= _CI_MAX_EXAMPLES_CEILING, (
        f"ci Hypothesis profile has max_examples={ci_profile.max_examples}, "
        f"must be <= {_CI_MAX_EXAMPLES_CEILING} (Hypothesis default). Issue #353."
    )

    assert ci_profile.deadline is not None, (
        "ci Hypothesis profile has deadline=None (no deadline). The profile "
        "must RAISE the deadline above Hypothesis' 200 ms default, not "
        f"disable it — set deadline >= {_CI_DEADLINE_FLOOR_MS} ms so a "
        "pathologically slow strategy still fails within the contract-test "
        "budget. Issue #353."
    )
    deadline_ms = ci_profile.deadline.total_seconds() * 1000
    assert deadline_ms >= _CI_DEADLINE_FLOOR_MS, (
        f"ci Hypothesis profile has deadline={deadline_ms} ms, "
        f"must be >= {_CI_DEADLINE_FLOOR_MS} ms to survive a noisy CI runner. "
        f"Issue #353."
    )


def test_ci_profile_is_deterministic() -> None:
    """The ``ci`` profile must run ``derandomize=True``.

    Deterministic seeds matter on CI because a flaky property-based test
    that only reproduces under one random seed is practically undiagnosable
    from the CI logs. Pinning ``derandomize=True`` makes every run of the
    same commit use the same seed, so a failure on CI reproduces locally.
    """
    ci_profile = hypothesis_settings.get_profile("ci")
    assert ci_profile.derandomize is True, (
        "ci Hypothesis profile must set derandomize=True so CI runs are "
        "reproducible locally. Issue #353."
    )


def test_active_profile_is_one_of_the_registered_profiles() -> None:
    """Something must have called ``load_profile`` during collection.

    Otherwise the active settings instance is Hypothesis' built-in
    ``default`` profile, and the whole point of registering ``ci`` and
    ``dev`` (bounded example count, generous deadline, deterministic
    seed) is undone the moment a future ``@schema.parametrize`` decorator
    lands.

    Implementation note: we compare a *fingerprint* (tuple of
    ``max_examples``/``deadline``/``derandomize``) of the active settings
    object against the fingerprints of the registered profiles, using
    only public Hypothesis APIs (``settings()`` for the active instance
    and ``settings.get_profile(name)`` for each registered name).
    Reading ``settings._current_profile`` directly would be shorter but
    relies on a private module-level attribute that Hypothesis can
    rename across versions — the contract suite would then start
    failing for reasons unrelated to our profiles. The fingerprint
    approach stays stable because the ``ci`` profile pins
    ``max_examples=50`` (!= default 100), ``deadline=5000ms`` (!= default
    200ms), and ``derandomize=True`` (!= default False), so a genuine
    "default profile is still active" regression always shows up on at
    least one fingerprint field.
    """
    active_settings = hypothesis_settings()
    default_settings = hypothesis_settings.get_profile("default")
    fingerprint_fields = ("max_examples", "deadline", "derandomize")

    def _fingerprint(profile: hypothesis_settings) -> tuple[object, ...]:
        return tuple(getattr(profile, field_name) for field_name in fingerprint_fields)

    active_fingerprint = _fingerprint(active_settings)
    registered_fingerprints = {
        name: _fingerprint(hypothesis_settings.get_profile(name)) for name in _REQUIRED_PROFILES
    }

    assert active_fingerprint != _fingerprint(default_settings), (
        "Active Hypothesis settings still match the built-in default profile on "
        f"{fingerprint_fields!r}; the contract conftest.py must call "
        "`settings.load_profile(...)` with a non-default registered profile. Issue #353."
    )

    assert active_fingerprint in registered_fingerprints.values(), (
        "Active Hypothesis settings do not match any registered contract-test profile on "
        f"{fingerprint_fields!r}. Expected one of {registered_fingerprints!r}, got "
        f"{active_fingerprint!r}. The contract conftest.py must call "
        "`settings.load_profile(...)` with a registered name. Issue #353."
    )

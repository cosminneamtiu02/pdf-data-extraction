"""Shared fixtures and Hypothesis profile for the contract-test suite.

Combines two concerns for the contract tree (kept together so there is
only one ``conftest.py`` at this level):

1. Issue #352 — session-scoped ``extract_schema`` fixture that builds
   the schemathesis ``BaseSchema`` against a throwaway FastAPI app
   exactly once per pytest session (not once per test, and not at
   module import time — the pre-#352 behavior leaked a
   ``tempfile.mkdtemp`` skills directory on every invocation, including
   bare ``--collect-only`` runs). The loader is a ``@contextmanager``
   named ``_build_extract_schema_session`` so the fixture is a thin
   three-line wrapper and the cleanup contract is directly exercisable
   from ``tests/unit/meta/test_contract_schema_fixture_cleanup.py``.

2. Issue #353 — Hypothesis profile registration. Schemathesis builds
   on Hypothesis, so every ``@schema.parametrize`` + stateful strategy
   introduced in this contract suite inherits whatever Hypothesis
   profile is active at collection time. Today the suite is hand-rolled
   (each ``test_extract_*`` is one-shot and ``validate_response`` is
   called directly), so zero Hypothesis examples actually run — but
   the moment a future PR adds ``@schema.parametrize``, the decorator
   silently inherits Hypothesis defaults (``max_examples=100``,
   ``deadline=200ms``) which would flake on CI and blow
   ``task test:contract``'s 300 s timeout. Registering named profiles
   here pins a known, bounded, deterministic budget.

Hypothesis profiles
-------------------
- ``ci``  : tight example budget (50), generous deadline (5 s),
  ``derandomize=True`` so a failure on CI reproduces locally bit-for-bit.
- ``dev`` : larger example budget (200), default deadline, non-
  derandomized for local fuzzing exploration.

Selection order: pytest ``--hypothesis-profile=<name>`` flag wins;
otherwise ``HYPOTHESIS_PROFILE`` env var; otherwise default ``ci``
(safest tightest budget for fresh clones and forgetful CI pipelines).

Pytest may import this ``conftest.py`` either before or after the
Hypothesis plugin's ``pytest_configure`` runs, depending on whether
it is loaded as an initial conftest or discovered during collection,
so conftest-import ordering alone is not what guarantees CLI
precedence. The guarantee comes from two cooperating mechanisms in
our own ``pytest_configure`` hook below:

* ``@pytest.hookimpl(trylast=True)`` pins our hook to run AFTER every
  other ``pytest_configure``, including the Hypothesis plugin's —
  regardless of import order — so by the time we look at
  ``config.getoption("hypothesis_profile")`` the CLI value is final.
* If that option is set, we ``return`` without calling ``load_profile``,
  leaving the plugin's CLI-driven selection intact.

We register profiles at import time (idempotent name/value storage)
but defer the ``load_profile`` call to ``pytest_configure`` so plain
``import tests.contract.conftest`` from a unit meta-test does not
mutate Hypothesis' global active profile. The
``_default_profile_is_still_active()`` helper exposed below is a
fingerprint probe used by the unit meta-suite to assert that the
hook actually switched profiles; it is NOT part of the CLI-precedence
path.

Why ``ci`` is the default and not ``dev``: an unknown caller (a fresh
clone, an IDE test-runner, a forgetful CI pipeline) should get the
safe profile automatically. Local developers who want the fuzzier
``dev`` profile opt in explicitly.

Why this lives in ``tests/contract/conftest.py`` and not the top-level
``tests/conftest.py``: Hypothesis has no footprint in the unit or
integration suites today, and scoping the profile registration to the
contract tree documents the intent (Hypothesis is only relevant to
Schemathesis) and avoids surprising anyone reading unit-test output.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import schemathesis
from hypothesis import settings as hypothesis_settings

from app.main import create_app
from tests.contract._helpers import settings as _settings
from tests.contract._helpers import write_valid_skill as _write_valid_skill

if TYPE_CHECKING:
    from collections.abc import Iterator


# --- Hypothesis profile registration (issue #353) -----------------------------

_CI_PROFILE = "ci"
_DEV_PROFILE = "dev"
_HYPOTHESIS_PROFILE_ENV_VAR = "HYPOTHESIS_PROFILE"
_REGISTERED_PROFILES: tuple[str, ...] = (_CI_PROFILE, _DEV_PROFILE)

# Register once at module import. `register_profile` is idempotent —
# calling it twice with the same name just overwrites the earlier entry —
# so repeated imports (e.g. from pytest collection walking the tree) are
# safe.
hypothesis_settings.register_profile(
    _CI_PROFILE,
    max_examples=50,
    deadline=5000,  # milliseconds; generous so CI's noisy runner doesn't flake
    derandomize=True,  # bit-for-bit reproducibility between CI and local runs
)
hypothesis_settings.register_profile(
    _DEV_PROFILE,
    max_examples=200,
    # `deadline` is intentionally left at the Hypothesis default (200 ms)
    # for ``dev``: locally, developers want the usual fast-fail loop;
    # they'd rather know a slow strategy exists than wait 5 s per example.
    derandomize=False,  # randomized locally — local devs want exploratory fuzzing
)


def _select_profile() -> str:
    """Return the Hypothesis profile name chosen by env var, or the ``ci`` default.

    This helper is a pure function of ``HYPOTHESIS_PROFILE``: it does
    NOT inspect Hypothesis' live global state. See ``_default_profile_is_still_active``
    for the "is the built-in default still active?" guard that decides
    whether the caller should honour the returned name.

    Resolution order (called only when ``--hypothesis-profile`` was
    NOT passed — the CLI-flag branch short-circuits the caller in
    ``pytest_configure`` before this helper runs):
    1. ``HYPOTHESIS_PROFILE`` environment variable. Used by local
       tooling and CI jobs that want a specific profile without
       threading a pytest flag through every invocation. The value is
       validated against the allow-list in ``_REGISTERED_PROFILES`` —
       an unknown name raises ``ValueError`` here rather than
       propagating through ``hypothesis_settings.load_profile`` as a
       terse ``InvalidArgument`` from the library.
    2. Default: ``ci``. Safest because it has the tightest budget.

    Raises:
        ValueError: if ``HYPOTHESIS_PROFILE`` is set to something other
            than a registered profile name. The message names the
            offending value AND the allow-list so the caller knows
            exactly what to fix.
    """
    env_choice = os.environ.get(_HYPOTHESIS_PROFILE_ENV_VAR)
    if not env_choice:
        return _CI_PROFILE
    if env_choice not in _REGISTERED_PROFILES:
        allowed = ", ".join(repr(name) for name in _REGISTERED_PROFILES)
        msg = (
            f"{_HYPOTHESIS_PROFILE_ENV_VAR}={env_choice!r} is not a registered "
            f"Hypothesis profile. Allowed values: {allowed}. "
            f"Unset the variable to use the default 'ci' profile."
        )
        raise ValueError(msg)
    return env_choice


def _default_profile_is_still_active() -> bool:
    """True iff Hypothesis' built-in ``default`` profile is the currently active one.

    Kept as a public helper (imported by
    ``tests/unit/meta/test_hypothesis_profile_selection.py``) because it
    is the fingerprint used to prove that the ``pytest_configure`` hook
    below actually switched the active profile away from Hypothesis'
    ``default``. Not called from this module — the hook uses
    ``config.getoption("hypothesis_profile")`` for CLI-flag detection,
    which is a reliable public pytest API and does not need a
    fingerprint heuristic. See the ``pytest_configure`` docstring for
    the full rationale.

    The fingerprint uses only public Hypothesis API
    (``settings()`` for the active instance, ``settings.get_profile("default")``
    for the built-in default) rather than the private
    ``settings._current_profile`` attribute — see the matching rationale
    in ``tests/contract/test_hypothesis_profile_registered.py``.
    """
    active = hypothesis_settings()
    default = hypothesis_settings.get_profile("default")
    return (
        active.max_examples == default.max_examples
        and active.deadline == default.deadline
        and active.derandomize == default.derandomize
    )


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    """Load the Hypothesis profile at pytest-configure time, honouring the CLI flag.

    Why a ``pytest_configure`` hook and not a module-import ``load_profile``
    call:

    * An explicit ``--hypothesis-profile=<name>`` on the pytest command
      line must win over our ``HYPOTHESIS_PROFILE`` / ``ci`` fallback.
      The Hypothesis pytest plugin's own ``pytest_configure`` reads that
      flag and calls ``settings.load_profile(...)`` itself. Running our
      own ``load_profile`` at conftest *import* time races the plugin:
      if the plugin ran first (initial-conftest ordering), our call
      overwrites the CLI choice. ``@pytest.hookimpl(trylast=True)``
      guarantees we run AFTER every other ``pytest_configure`` hook,
      including the Hypothesis plugin's, so ``config.getoption`` reads
      the final CLI-resolved state.
    * Querying ``config.getoption("hypothesis_profile")`` is a reliable,
      public pytest API. It returns the raw CLI value (``None`` if the
      flag was not passed), independent of whether the Hypothesis plugin
      has already acted on it. No fingerprint heuristic needed.
    * Deferring the ``load_profile`` call keeps conftest import itself
      side-effect-free. Plain ``import tests.contract.conftest`` from a
      unit meta-test no longer mutates Hypothesis' global active profile
      — the mutation now happens inside ``pytest_configure``, which the
      unit suite never triggers for the contract conftest.

    Registration (``register_profile`` calls at module top) stays at
    import time because it is idempotent key/value storage — registering
    the same name twice just overwrites the earlier entry, and the meta
    tests that import this module need the profiles to exist for
    ``settings.get_profile("ci")`` to succeed without running pytest.

    The ``ValueError`` from ``_select_profile()`` for a bogus
    ``HYPOTHESIS_PROFILE`` still surfaces here (same friendly message
    as before) because the selector is the same helper; the only
    difference is *when* it runs.
    """
    # ``config.getoption`` returns ``None`` when ``--hypothesis-profile``
    # was not passed, and the Hypothesis pytest plugin is what registers
    # the option. ``default=None`` protects against the vanishingly
    # unlikely case where the plugin is disabled (``-p no:hypothesispytest``):
    # pytest would otherwise raise ``ValueError: no option named
    # 'hypothesis_profile'``.
    explicit_profile = config.getoption("hypothesis_profile", default=None)
    if explicit_profile:
        # The Hypothesis plugin has already loaded this profile in its
        # own ``pytest_configure``; ``trylast=True`` pins us after it.
        # Honor the CLI flag by leaving that selection intact.
        return
    hypothesis_settings.load_profile(_select_profile())


# --- schemathesis extract_schema fixture (issue #352) -------------------------


@contextmanager
def _build_extract_schema_session() -> Iterator[tuple[schemathesis.BaseSchema, Path]]:
    """Build the schemathesis schema against a throwaway app, with guaranteed cleanup.

    Yields ``(schema, skills_dir)`` so callers (the session fixture *and*
    the meta-test) can both use the schema and inspect the skills
    directory that backs it.

    Uses ``tempfile.TemporaryDirectory`` rather than pytest's
    ``tmp_path_factory`` to get deterministic ``__exit__`` cleanup.
    ``tmp_path_factory`` retains the last three session roots by
    default, which is fine for test debuggability but would make the
    "tempdir is gone after the yield" contract non-load-bearing — the
    directory would still be on disk until pytest rotated it out.
    Explicit ``TemporaryDirectory()`` removes the directory
    unconditionally the moment the ``with`` block exits, so the
    cleanup assertion in the meta-test is a real probe.

    The schema is built against a dedicated ``create_app(Settings(...))``
    rather than the process-wide ``app``: if the ambient environment
    has ``APP_ENV=production``, ``create_app`` disables
    ``/openapi.json`` and ``from_asgi`` would 404. Pinning
    ``app_env="development"`` via ``_settings`` (see
    ``tests/contract/_helpers.py``) makes the fixture robust to
    ambient env vars.
    """
    with tempfile.TemporaryDirectory(prefix="pdfx_contract_extract_schema_") as tmpdir:
        skills_dir = Path(tmpdir)
        # `create_app` validates skill YAMLs at startup, so a minimal
        # valid `invoice@1` must exist before `from_asgi` spins the app.
        _write_valid_skill(skills_dir)
        schema_app = create_app(_settings(skills_dir))
        # `openapi.from_asgi` uses `starlette_testclient.TestClient`
        # synchronously under the hood; running this inside an
        # `async def` test would deadlock against the outer
        # pytest-asyncio loop. Session-scope + sync loader is the
        # supported pattern.
        schema = schemathesis.openapi.from_asgi("/openapi.json", schema_app)
        yield schema, skills_dir


@pytest.fixture(scope="session")
def extract_schema() -> Iterator[schemathesis.BaseSchema]:
    """Session-scoped schemathesis schema for ``POST /api/v1/extract``.

    Loaded exactly once per session and shared across every contract
    test that needs ``validate_response``. The OpenAPI contract for
    ``/api/v1/extract`` is identical across the per-test ``Settings``
    variations the test apps use (declared response shapes don't
    depend on runtime config), so one session-scoped schema is correct.
    """
    with _build_extract_schema_session() as (schema, _skills_dir):
        yield schema

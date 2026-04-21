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

Selection order: pytest ``--hypothesis-profile=<name>`` flag (the
Hypothesis pytest plugin reads this in ``pytest_configure``, which
runs AFTER conftest import, so CLI wins), then ``HYPOTHESIS_PROFILE``
env var, then default ``ci`` (safest tightest budget for fresh clones
and forgetful CI pipelines).

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
    """Return the Hypothesis profile name to activate at collection time.

    Resolution order:
    1. ``--hypothesis-profile=<name>`` on the pytest command line — NOT
       read here. The Hypothesis pytest plugin reads the flag itself
       during ``pytest_configure``, which runs AFTER this conftest
       import. If the flag was passed, the plugin will overwrite
       whatever profile we loaded below, so CLI wins.
    2. ``HYPOTHESIS_PROFILE`` environment variable. Used by local
       tooling and CI jobs that want a specific profile without
       threading a pytest flag through every invocation.
    3. Default: ``ci``. Safest because it has the tightest budget.
    """
    env_choice = os.environ.get(_HYPOTHESIS_PROFILE_ENV_VAR)
    if env_choice:
        return env_choice
    return _CI_PROFILE


# Load a profile at import time so any future ``@schema.parametrize``
# decorator inherits a bounded budget from the moment pytest collects
# it. The Hypothesis pytest plugin overwrites this in
# ``pytest_configure`` when ``--hypothesis-profile=<name>`` is passed —
# that's the intended behaviour: CLI wins over our default.
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

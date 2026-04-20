"""Filtered dotenv source for :class:`app.core.config.Settings` (issue #271).

Why this exists
---------------

Issue #271 wanted ``extra="forbid"`` on :class:`app.core.config.Settings` so a
typo in a ``Settings(...)`` constructor call (e.g. ``Settings(olama_model=x)``)
raises ``ValidationError`` instead of silently no-op'ing. pydantic-settings
applies ``extra="forbid"`` uniformly across ALL sources, including the dotenv
source, which creates a collision with our shared-``.env`` layout.

The shared-``.env`` layout, confirmed by ``tests/unit/core/test_env_example_parity.py``,
is:

* Both :class:`Settings` and
  :class:`scripts._benchmark_settings.BenchmarkSettings` read the same
  ``apps/backend/.env`` file.
* :class:`BenchmarkSettings` owns all ``BENCH_*`` keys (``env_prefix="BENCH_"``),
  and ``.env.example`` lists them (issues #237, #272).
* New developers follow ``docs/new-project-setup.md`` and run
  ``cp .env.example .env``, which seeds the file with ``BENCH_*`` keys.

Without filtering, ``Settings(_env_file=".env")`` would raise seven
``extra_forbidden`` errors at startup on every fresh dev clone, regressing
onboarding. Switching the whole class to ``extra="ignore"`` would trade the
new guard (catches constructor-kwarg typos) for the old bug it was meant to
fix. This source threads the needle: it strips dotenv keys that are not
declared on :class:`Settings` BEFORE they reach validation, so:

* Unknown ``.env`` keys (``BENCH_WARMUP``, ``OLLMA_MODEL``): silently ignored,
  matching the existing behavior for typoed shell env vars (pydantic-settings
  only looks up names matching declared fields).
* Unknown constructor kwargs (``Settings(olama_model=x)``): still raise
  ``extra_forbidden`` because the filter only touches the dotenv source.
* Known keys (``OLLAMA_MODEL``, ``LOG_LEVEL``): loaded normally.

The parity test in ``tests/unit/core/test_env_example_parity.py`` remains
the guard that catches typoed keys added to ``.env.example`` at commit time —
it asserts every ``KEY=`` line maps to a declared field on some managed
``BaseSettings`` subclass.
"""

from __future__ import annotations

from typing import Any

from pydantic_settings import DotEnvSettingsSource


class FilteredDotEnvSettingsSource(DotEnvSettingsSource):
    """Dotenv source that drops keys not declared on the settings class.

    Overrides :meth:`DotEnvSettingsSource.__call__` to post-filter its output
    against the target :class:`BaseSettings` subclass's declared field names.
    The upstream ``__call__`` already maps known env names to their field
    names and leaves unknown env names under their raw UPPER_CASE key — we
    simply strip those unknown entries before the data reaches pydantic
    validation, so ``extra="forbid"`` never sees them.

    Lowercasing is correct for this project because :class:`Settings` does
    not set ``case_sensitive=True``; pydantic-settings default-lowercases
    matched field inputs before the ``__call__`` return.
    """

    def __call__(self) -> dict[str, Any]:
        """Return only dotenv entries whose key is a declared field name.

        The base implementation returns a mix of:

        * Declared field names with their resolved values (e.g.
          ``ollama_model``, ``cors_origins``).
        * Raw UPPER_CASE keys for dotenv entries that did not match any
          declared field (e.g. ``BENCH_WARMUP`` when ``Settings`` has no
          ``bench_warmup`` field).

        The declared-field entries must pass through untouched — they ARE
        the values :class:`Settings` needs. The raw UPPER_CASE entries are
        what would trip ``extra="forbid"``; dropping them here preserves
        the guard for constructor kwargs while letting ``.env`` be shared
        with other :class:`BaseSettings` subclasses (issue #271).
        """
        data = super().__call__()
        declared = set(self.settings_cls.model_fields)
        return {key: value for key, value in data.items() if key in declared}

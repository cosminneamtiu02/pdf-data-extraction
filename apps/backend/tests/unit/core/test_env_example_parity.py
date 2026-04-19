"""Parity check between ``Settings``/``BenchmarkSettings`` and
``apps/backend/.env.example``.

Guards the CLAUDE.md cross-cutting rule: "Never add an env var without adding
to both ``Settings`` and ``apps/backend/.env.example``." Closes the drift
vector that motivated issue #138 so future additions cannot silently regress.

The test iterates every field on every ``BaseSettings`` subclass we manage,
converts each name to ``UPPER_SNAKE_CASE`` with the class's ``env_prefix``
applied, and asserts that a matching ``KEY=`` line exists at the start of a
line in ``.env.example``. Any field that is intentionally not environment-
configurable must be added to ``_WAIVED_FIELDS`` with a comment explaining why.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic_settings import BaseSettings

from app.core.config import Settings
from scripts._benchmark_settings import BenchmarkSettings

# Fields that intentionally do NOT appear in ``.env.example``. Empty today;
# add entries with an inline comment explaining the waiver if the need ever
# arises (e.g. fields derived from other env vars at runtime).
_WAIVED_FIELDS: frozenset[str] = frozenset()

# The pydantic-settings classes that own the ``.env.example`` contract. Each
# class contributes ``{env_prefix}{FIELD_NAME_UPPER}`` keys to the parity check.
# Added BenchmarkSettings when issue #237 migrated the benchmark CLI off
# ``os.environ`` and onto pydantic-settings.
_SETTINGS_CLASSES: tuple[type[BaseSettings], ...] = (Settings, BenchmarkSettings)

# ``apps/backend/tests/unit/core/test_env_example_parity.py``
#   parents[0] = ``apps/backend/tests/unit/core/``
#   parents[1] = ``apps/backend/tests/unit/``
#   parents[2] = ``apps/backend/tests/``
#   parents[3] = ``apps/backend/``
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_ENV_EXAMPLE = _BACKEND_ROOT / ".env.example"


def _declared_env_keys() -> frozenset[str]:
    """Return the set of ``KEY=`` prefixes declared in ``.env.example``."""
    pattern = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    return frozenset(pattern.findall(text))


def _expected_env_keys() -> frozenset[str]:
    """Return every env-var key declared by our settings classes.

    Applies each class's ``env_prefix`` (falling back to empty string) so that
    ``BenchmarkSettings.url`` maps to ``BENCH_URL`` and ``Settings.app_env``
    maps to ``APP_ENV``.
    """
    expected: set[str] = set()
    for cls in _SETTINGS_CLASSES:
        prefix_raw = cls.model_config.get("env_prefix") or ""
        prefix = str(prefix_raw).upper()
        for name in cls.model_fields:
            if name in _WAIVED_FIELDS:
                continue
            expected.add(f"{prefix}{name.upper()}")
    return frozenset(expected)


def test_env_example_covers_every_settings_field() -> None:
    """Every field across our settings classes maps to a ``KEY=`` line."""
    declared = _declared_env_keys()
    missing = sorted(_expected_env_keys() - declared)
    assert missing == [], (
        f"Settings fields missing from apps/backend/.env.example: {missing}. "
        "Either add a `KEY=<example value>` line with a one-line comment, or "
        "add the field to _WAIVED_FIELDS with a justification."
    )


def test_env_example_has_no_unknown_keys() -> None:
    """Every key in ``.env.example`` corresponds to a managed Settings field."""
    declared = _declared_env_keys()
    unknown = sorted(declared - _expected_env_keys())
    assert unknown == [], (
        f".env.example declares keys not found on any settings class: {unknown}. "
        "Either remove the stale keys or add the matching field to a "
        "BaseSettings subclass in _SETTINGS_CLASSES."
    )

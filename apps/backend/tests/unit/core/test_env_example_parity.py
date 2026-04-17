"""Parity check between ``Settings`` fields and ``apps/backend/.env.example``.

Guards the CLAUDE.md cross-cutting rule: "Never add an env var without adding
to both ``Settings`` and ``apps/backend/.env.example``." Closes the drift
vector that motivated issue #138 so future additions cannot silently regress.

The test iterates every field on ``Settings``, converts each name to
``UPPER_SNAKE_CASE`` (pydantic-settings' default env-var convention), and
asserts that a matching ``KEY=`` line exists at the start of a line in
``.env.example``. Any field that is intentionally not environment-configurable
must be added to ``_WAIVED_FIELDS`` with a comment explaining why.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

# Fields that intentionally do NOT appear in ``.env.example``. Empty today;
# add entries with an inline comment explaining the waiver if the need ever
# arises (e.g. fields derived from other env vars at runtime).
_WAIVED_FIELDS: frozenset[str] = frozenset()

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


def test_env_example_covers_every_settings_field() -> None:
    """Every ``Settings`` field maps to a ``KEY=`` line in ``.env.example``."""
    declared = _declared_env_keys()
    missing = sorted(
        name.upper()
        for name in Settings.model_fields
        if name not in _WAIVED_FIELDS and name.upper() not in declared
    )
    assert missing == [], (
        f"Settings fields missing from apps/backend/.env.example: {missing}. "
        "Either add a `KEY=<example value>` line with a one-line comment, or "
        "add the field to _WAIVED_FIELDS with a justification."
    )


def test_env_example_has_no_unknown_keys() -> None:
    """Every key in ``.env.example`` corresponds to a ``Settings`` field."""
    declared = _declared_env_keys()
    known = {name.upper() for name in Settings.model_fields}
    unknown = sorted(declared - known)
    assert unknown == [], (
        f".env.example declares keys not found on Settings: {unknown}. "
        "Either remove the stale keys or add the matching field to Settings."
    )

"""Regression: ``Settings`` explicitly sets ``extra="forbid"`` (issue #271).

Without an explicit declaration, the guardrail against typoed env-var names
(e.g. ``OLAMA_MODEL=gemma4:e2b``) relies on whatever pydantic-settings ships
as its default. The default is ``"forbid"`` in pydantic-settings 2.13, but
that is an implementation detail — a future upgrade could relax it to
``"ignore"`` and silently restore the silent-swallow bug described in the
issue. Locking the value explicitly in ``SettingsConfigDict`` (and asserting
it here) makes the contract part of the source of truth.

The behavioral assertion (constructing ``Settings(unknown_field=...)``
raises ``pydantic.ValidationError`` with ``extra_forbidden``) double-locks
the invariant: even if a developer later removes the explicit line, this
test fails loudly instead of a typoed env var silently reverting to defaults
in production.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_config_sets_extra_forbid_explicitly() -> None:
    """``SettingsConfigDict`` must spell ``extra="forbid"`` explicitly.

    Relying on the pydantic-settings inherited default is fragile across
    upstream upgrades; the declaration needs to be part of this repo's
    source of truth.
    """
    assert Settings.model_config.get("extra") == "forbid"


def test_settings_rejects_unknown_kwargs() -> None:
    """Unknown field names raise ``ValidationError`` with ``extra_forbidden``.

    Locks in the semantic intent of issue #271: typos never silently fall
    back to defaults. Asserts on ``type == "extra_forbidden"`` (the pydantic
    error code documented at https://errors.pydantic.dev/2.13/v/extra_forbidden)
    rather than the human-readable message, so wording tweaks in future
    pydantic releases do not break the test.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(olama_model="typo_goes_here")  # type: ignore[call-arg]

    error_types = [err["type"] for err in exc_info.value.errors()]
    assert "extra_forbidden" in error_types, (
        f"Expected 'extra_forbidden' in {error_types}; "
        "Settings must reject unknown fields instead of silently ignoring them."
    )

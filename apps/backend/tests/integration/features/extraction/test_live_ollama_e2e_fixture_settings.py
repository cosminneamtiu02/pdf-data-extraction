"""Regression guard for issue #405.

The live-Ollama e2e fixture ``_ollama_reachable_fixture`` constructs a
``Settings`` object to drive the reachability probe. The test body (in
``test_live_ollama_e2e.py``) constructs its *own* ``Settings`` with
``app_env="development"`` so extraction runs in development mode
regardless of how the CI/dev host is configured. If the probe fixture
reads ``APP_ENV`` from the ambient environment and the test body pins
it to ``"development"``, the two are inconsistent — and on a host with
``APP_ENV=production`` that inconsistency is silent.

This module asserts the fixture pins ``app_env`` explicitly so it
matches the test body and is reproducible regardless of host env.

Kept separate from ``test_live_ollama_e2e.py`` because that module is
``pytest.mark.slow`` at module scope and is excluded from the default
``task check`` run; this assertion must run in the fast default suite
so a regression is caught without requiring the slow gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.integration.features.extraction import test_live_ollama_e2e as e2e

if TYPE_CHECKING:
    import pytest

    from app.core.config import Settings


def test_ollama_reachable_fixture_pins_app_env_to_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reachability fixture's ``Settings`` must pin ``app_env='development'``.

    Simulates a CI/dev host leaking ``APP_ENV=production`` into the
    process env, then invokes the fixture function directly and
    inspects the ``Settings`` object handed to the probe. The fixture
    must override the ambient env var so it matches the test body.
    """
    monkeypatch.setenv("APP_ENV", "production")

    captured: dict[str, Settings] = {}

    def _spy_reachable(settings: Settings) -> tuple[bool, str]:
        captured["settings"] = settings
        # Return reachable=True so the fixture does not call
        # pytest.skip(...) and short-circuit the test.
        return True, ""

    monkeypatch.setattr(e2e, "_ollama_reachable", _spy_reachable)

    # ``@pytest.fixture`` wraps the function; ``__wrapped__`` exposes the
    # underlying callable for direct invocation (pytest forbids calling
    # the wrapper directly). This is the standard unit-test pattern for
    # asserting on a fixture's body without scheduling it via pytest.
    e2e._ollama_reachable_fixture.__wrapped__()  # type: ignore[attr-defined]  # noqa: SLF001 — unit-testing a module-private fixture's body

    assert "settings" in captured, "fixture did not call _ollama_reachable"
    assert captured["settings"].app_env == "development", (
        f"expected fixture to pin app_env='development' to match the test body; "
        f"got {captured['settings'].app_env!r}"
    )

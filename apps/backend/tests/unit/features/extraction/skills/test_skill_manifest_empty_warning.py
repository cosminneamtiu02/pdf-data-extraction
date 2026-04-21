"""Warning emitted on ``SkillManifest.lookup`` against an empty manifest.

Issue #386: when the manifest holds zero skills, every extraction request
falls through to ``SkillNotFoundError``. That error is semantically correct
but drowns the root cause (no skills ever loaded) in a per-request "skill
missing" message. The fix emits a ``skill_lookup_on_empty_manifest`` warning
*before* raising the existing ``SkillNotFoundError`` so operators see the
signal in-stream with the failing request instead of having to correlate
with the ``/ready`` probe.
"""

import pytest

from app.exceptions import SkillNotFoundError
from app.features.extraction.skills import skill_manifest as skill_manifest_module
from app.features.extraction.skills.skill_manifest import SkillManifest
from tests.conftest import make_skill


class _SpyLogger:
    """Test double for ``skill_manifest_module._logger``.

    Why we don't use ``structlog.testing.capture_logs()`` here: the
    ``skill_manifest`` module binds ``_logger`` at import time and our
    ``configure_logging()`` registers ``cache_logger_on_first_use=True``.
    A sibling test touching the real logger outside a ``capture_logs()``
    context can cache a bound logger that later ``capture_logs()``
    contexts won't see — making log-assertion tests order-dependent.
    The ``_SpyLogger`` pattern (used in ``test_router.py`` and
    ``test_extraction_service.py``) sidesteps structlog's global state
    entirely.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:  # pragma: no cover
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))

    def error(self, event: str, **kwargs: object) -> None:  # pragma: no cover
        self.events.append((event, kwargs))


def test_empty_manifest_lookup_emits_warning_before_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty-manifest lookup logs ``skill_lookup_on_empty_manifest`` and raises.

    The raised error stays ``SkillNotFoundError`` — the fix only adds the
    in-stream warning, it never changes the response payload or the raised
    error type (issue #386).
    """
    spy = _SpyLogger()
    monkeypatch.setattr(skill_manifest_module, "_logger", spy)
    manifest = SkillManifest({})

    with pytest.raises(SkillNotFoundError):
        manifest.lookup("invoice", "1")

    event = next(
        (kwargs for name, kwargs in spy.events if name == "skill_lookup_on_empty_manifest"),
        None,
    )
    assert event is not None, (
        f"expected 'skill_lookup_on_empty_manifest' log event, got {spy.events!r}"
    )
    assert event["requested_name"] == "invoice"
    assert event["requested_version"] == "1"


def test_empty_manifest_lookup_latest_version_also_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The empty-manifest warning fires regardless of the requested version form.

    The ``latest`` resolution branch was a distinct early-exit before this
    fix; the warning must cover it too so the signal is consistent.
    """
    spy = _SpyLogger()
    monkeypatch.setattr(skill_manifest_module, "_logger", spy)
    manifest = SkillManifest({})

    with pytest.raises(SkillNotFoundError):
        manifest.lookup("invoice", "latest")

    names = [name for name, _ in spy.events]
    assert "skill_lookup_on_empty_manifest" in names


def test_populated_manifest_missing_skill_does_not_warn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-empty manifest looking up a missing skill MUST NOT emit the warning.

    The warning is scoped to the "no skills ever loaded" failure mode.
    Ordinary misses against a populated manifest are not operator signals
    and should stay silent on this channel to avoid log noise.
    """
    spy = _SpyLogger()
    monkeypatch.setattr(skill_manifest_module, "_logger", spy)
    manifest = SkillManifest({("invoice", 1): make_skill("invoice", 1)})

    with pytest.raises(SkillNotFoundError):
        manifest.lookup("mystery", "1")

    names = [name for name, _ in spy.events]
    assert "skill_lookup_on_empty_manifest" not in names

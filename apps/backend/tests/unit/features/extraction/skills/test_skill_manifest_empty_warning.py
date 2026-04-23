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
from tests._support.skill_factory import make_skill


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


def test_empty_manifest_warning_logged_only_once_across_multiple_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated lookups against the same empty manifest warn exactly once.

    ``lookup`` is on the request path. A misconfigured deployment that keeps
    receiving traffic would otherwise emit one warning per request, flooding
    the log stream with the same "no skills ever loaded" event. The manifest
    instance remembers whether it has warned so operators get the signal
    once, not per-request (PR #493 Copilot feedback).

    The raise behavior is unchanged — every lookup still raises
    ``SkillNotFoundError`` because that's the correct per-request response.
    """
    spy = _SpyLogger()
    monkeypatch.setattr(skill_manifest_module, "_logger", spy)
    manifest = SkillManifest({})

    for _ in range(3):
        with pytest.raises(SkillNotFoundError):
            manifest.lookup("invoice", "1")

    empty_warnings = [
        kwargs for name, kwargs in spy.events if name == "skill_lookup_on_empty_manifest"
    ]
    assert len(empty_warnings) == 1, (
        f"expected exactly one empty-manifest warning across 3 lookups, got {spy.events!r}"
    )


def test_empty_manifest_warning_once_covers_latest_and_integer_and_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three version-branch miss paths share the same once-per-instance flag.

    The three raise sites — ``latest`` unresolved, integer parse failure,
    integer not present — each need to consult the flag so the first miss
    in any form suppresses the warning for the remaining misses.
    """
    spy = _SpyLogger()
    monkeypatch.setattr(skill_manifest_module, "_logger", spy)
    manifest = SkillManifest({})

    with pytest.raises(SkillNotFoundError):
        manifest.lookup("invoice", "latest")
    with pytest.raises(SkillNotFoundError):
        manifest.lookup("invoice", "1")
    with pytest.raises(SkillNotFoundError):
        manifest.lookup("invoice", "abc")

    empty_warnings = [
        kwargs for name, kwargs in spy.events if name == "skill_lookup_on_empty_manifest"
    ]
    assert len(empty_warnings) == 1, (
        f"expected exactly one empty-manifest warning across all branches, got {spy.events!r}"
    )


def test_empty_manifest_non_integer_version_preserves_value_error_chain() -> None:
    """Non-integer ``version`` on an empty manifest still chains ``ValueError``.

    Before PR #493's early-return fix, an invalid version string like
    ``"abc"`` would raise ``SkillNotFoundError(...) from ValueError`` because
    the parse failure propagated through. The PR #493 Copilot feedback
    flagged that the early-return short-circuit silently dropped that
    chaining on empty manifests — consistent debugging requires the chain
    be preserved regardless of manifest emptiness.
    """
    manifest = SkillManifest({})

    with pytest.raises(SkillNotFoundError) as exc_info:
        manifest.lookup("invoice", "not-a-number")

    assert isinstance(exc_info.value.__cause__, ValueError), (
        f"expected SkillNotFoundError.__cause__ to be ValueError, got {exc_info.value.__cause__!r}"
    )

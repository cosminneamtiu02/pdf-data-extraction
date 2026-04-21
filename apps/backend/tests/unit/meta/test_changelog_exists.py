"""Meta-guardrail: CHANGELOG.md exists at the repo root.

Issue #413: the repo previously had no CHANGELOG and release history lived
only in the commit log. This project is a reusable microservice meant to be
embedded in downstream projects, so consumers need a changelog to know which
version they should pin.

This test pins three minimum hygiene invariants:

1. ``CHANGELOG.md`` exists at the repo root.
2. Its first non-empty line is ``# Changelog`` (Keep a Changelog 1.1.0 form).
3. It contains an ``## [Unreleased]`` section so in-flight changes have a
   home without forcing a version bump.

It deliberately does NOT assert the sub-category headings (``### Added`` etc.)
or any version sections -- those are editorial concerns that should evolve
naturally as real releases get cut.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
_CHANGELOG_PATH = _REPO_ROOT / "CHANGELOG.md"
_MISSING_CHANGELOG_MESSAGE = (
    f"Expected CHANGELOG.md at {_CHANGELOG_PATH}. "
    "See issue #413: the repo must track release history in a Keep a "
    "Changelog file, not only in the commit log."
)


def _read_changelog_or_fail() -> str:
    """Return CHANGELOG.md contents, or call ``pytest.fail`` if missing.

    Centralises the file-existence precondition so the header and
    ``[Unreleased]`` tests emit an actionable failure instead of a
    ``FileNotFoundError`` traceback when ``CHANGELOG.md`` is absent.
    The existence invariant itself is owned by
    ``test_changelog_file_exists_at_repo_root``; this helper mirrors the
    check so downstream tests remain independently readable. The diagnostic
    string lives in ``_MISSING_CHANGELOG_MESSAGE`` so both call sites share
    wording and cannot drift apart.
    """
    if not _CHANGELOG_PATH.is_file():
        pytest.fail(_MISSING_CHANGELOG_MESSAGE)
    return _CHANGELOG_PATH.read_text(encoding="utf-8")


def test_changelog_file_exists_at_repo_root() -> None:
    assert _CHANGELOG_PATH.is_file(), _MISSING_CHANGELOG_MESSAGE


def test_changelog_has_top_level_header() -> None:
    content = _read_changelog_or_fail()
    first_non_empty = next(
        (line.rstrip() for line in content.splitlines() if line.strip()),
        "",
    )
    assert first_non_empty == "# Changelog", (
        f"CHANGELOG.md at {_CHANGELOG_PATH} must open with '# Changelog' as "
        f"its first non-empty line (got {first_non_empty!r}). Follow the "
        "Keep a Changelog 1.1.0 format."
    )


def test_changelog_has_unreleased_section() -> None:
    content = _read_changelog_or_fail()
    has_unreleased_header = any(line.strip() == "## [Unreleased]" for line in content.splitlines())
    assert has_unreleased_header, (
        f"CHANGELOG.md at {_CHANGELOG_PATH} is missing a line that is exactly "
        "'## [Unreleased]'. Substring matches are rejected so code blocks or "
        "explanatory prose cannot satisfy this invariant. New changes must "
        "land under [Unreleased] until a version is cut."
    )

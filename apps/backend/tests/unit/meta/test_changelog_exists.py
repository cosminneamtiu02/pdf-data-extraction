"""Meta-guardrail: CHANGELOG.md exists at the repo root.

Issue #413: the repo previously had no CHANGELOG and release history lived
only in the commit log. This project is a reusable microservice meant to be
embedded in downstream projects, so consumers need a changelog to know which
version they should pin.

This test pins three minimum hygiene invariants:

1. ``CHANGELOG.md`` exists at the repo root.
2. It opens with the ``# Changelog`` header (Keep a Changelog 1.1.0 form).
3. It contains an ``## [Unreleased]`` section so in-flight changes have a
   home without forcing a version bump.

It deliberately does NOT assert the sub-category headings (``### Added`` etc.)
or any version sections — those are editorial concerns that should evolve
naturally as real releases get cut.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
_CHANGELOG_PATH = _REPO_ROOT / "CHANGELOG.md"


def test_changelog_file_exists_at_repo_root() -> None:
    assert _CHANGELOG_PATH.is_file(), (
        f"Expected CHANGELOG.md at {_CHANGELOG_PATH}. "
        "See issue #413: the repo must track release history in a Keep a "
        "Changelog file, not only in the commit log."
    )


def test_changelog_has_top_level_header() -> None:
    content = _CHANGELOG_PATH.read_text(encoding="utf-8")
    assert "# Changelog" in content, (
        f"CHANGELOG.md at {_CHANGELOG_PATH} is missing the '# Changelog' "
        "header. Follow the Keep a Changelog 1.1.0 format."
    )


def test_changelog_has_unreleased_section() -> None:
    content = _CHANGELOG_PATH.read_text(encoding="utf-8")
    assert "## [Unreleased]" in content, (
        f"CHANGELOG.md at {_CHANGELOG_PATH} is missing the '## [Unreleased]' "
        "section. New changes must land under [Unreleased] until a version "
        "is cut."
    )

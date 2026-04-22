"""Hygiene check: allowlisted historical docs must carry the canonical banner.

Issue #414 observed that a new contributor browsing ``docs/*.md``
alphabetically cannot distinguish current operational guidance from
historical artifacts unless each historical document explicitly
self-declares its status. ``docs/bootstrap-decisions.md`` set the
precedent with a ``> **Document status: Historical**`` blockquote
directly under the title; ``docs/reshape-plan.md`` was brought into
line by #414.

This meta-test pins the banner on the small allowlist below so a future
edit cannot silently strip the marker and re-introduce the "looks
current, is actually historical" ambiguity. It deliberately checks only
the exact, byte-for-byte phrase — anything softer (e.g. "Status:
Historical" without the ``Document`` prefix) would split the convention
into two near-duplicates, which is itself a paradigm-drift hazard under
CLAUDE.md Sacred Rule 3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from ._linter_subprocess import REPO_ROOT

_DOCS_DIR: Final[Path] = REPO_ROOT / "docs"
_BANNER_MARKER: Final[str] = "> **Document status: Historical**"
_HISTORICAL_DOCS: Final[frozenset[str]] = frozenset(
    {
        "bootstrap-decisions.md",
        "reshape-plan.md",
    }
)


def test_historical_docs_carry_canonical_banner() -> None:
    """Every allowlisted historical doc must contain the exact banner marker."""
    missing: list[str] = []
    for filename in sorted(_HISTORICAL_DOCS):
        doc_path = _DOCS_DIR / filename
        assert doc_path.is_file(), f"expected historical doc at {doc_path}"
        text = doc_path.read_text(encoding="utf-8")
        if _BANNER_MARKER not in text:
            missing.append(filename)
    assert not missing, (
        f"historical doc(s) {missing} must carry the exact banner "
        f"{_BANNER_MARKER!r} directly under the title (see issue #414)"
    )

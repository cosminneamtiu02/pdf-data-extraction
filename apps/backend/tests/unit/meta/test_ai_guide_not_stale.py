"""Guard rail preventing docs/ai-guide.md from drifting back into staleness.

Issue #367 observed that `docs/ai-guide.md` still described the repo as a
"post-bootstrap shell" with no extraction feature, even though
`apps/backend/app/features/extraction/` and its subpackages have been
implemented. A new AI agent reading the guide at session start would be
actively misled.

This test is the mechanical guard that keeps the guide honest. It asserts:

1. None of the known stale claims (phrasings taken verbatim from the
   pre-#367 version) reappear in the guide.
2. Every subpackage that actually exists under
   `apps/backend/app/features/extraction/` is mentioned by name in the
   guide, so adding a new subpackage without updating the guide will fail
   this test.

When this test fails, update `docs/ai-guide.md` to reflect current reality
instead of silencing the test.
"""

from __future__ import annotations

from tests._paths import EXTRACTION_ROOT as _EXTRACTION_FEATURE_DIR
from tests._paths import REPO_ROOT as _REPO_ROOT

_AI_GUIDE_PATH = _REPO_ROOT / "docs" / "ai-guide.md"

# Phrases copied verbatim from the stale ai-guide.md at commit-of-issue-#367.
# Each entry is a lowercased substring; matching is case-insensitive.
_STALE_PHRASES: tuple[str, ...] = (
    "features/extraction/ subpackage does not exist",
    "no extraction feature (yet)",
    "the `apps/backend/skills/` data directory does not exist",
    "no skill yamls",
)


def _ai_guide_text_lower() -> str:
    assert _AI_GUIDE_PATH.is_file(), (
        f"Expected AI guide at {_AI_GUIDE_PATH}, but it does not exist. "
        "If docs/ai-guide.md was moved or renamed, update this test to point "
        "to the new location."
    )
    return _AI_GUIDE_PATH.read_text(encoding="utf-8").lower()


def test_ai_guide_does_not_contain_stale_phrases() -> None:
    """No known stale phrasing from pre-#367 may re-appear in the guide."""
    text_lower = _ai_guide_text_lower()
    offending = [phrase for phrase in _STALE_PHRASES if phrase.lower() in text_lower]
    assert not offending, (
        "docs/ai-guide.md contains stale phrasing: "
        f"{offending!r}. Update the guide to describe what exists today "
        "rather than reintroducing pre-#367 claims."
    )


def test_ai_guide_mentions_every_existing_extraction_subpackage() -> None:
    """Every implemented extraction subpackage must be named in the guide."""
    text_lower = _ai_guide_text_lower()
    assert _EXTRACTION_FEATURE_DIR.is_dir(), (
        f"Expected extraction feature directory at {_EXTRACTION_FEATURE_DIR}, "
        "but it does not exist. If the extraction feature was relocated or "
        "renamed, update this test to point to the new location."
    )
    subpackages = sorted(
        entry.name
        for entry in _EXTRACTION_FEATURE_DIR.iterdir()
        if entry.is_dir() and not entry.name.startswith("_") and (entry / "__init__.py").is_file()
    )
    assert subpackages, (
        f"No subpackages found under {_EXTRACTION_FEATURE_DIR}; the "
        "extraction feature layout has changed and this test must be updated."
    )
    missing = [name for name in subpackages if name.lower() not in text_lower]
    assert not missing, (
        f"docs/ai-guide.md does not mention these implemented extraction "
        f"subpackages: {missing!r}. Add them to the guide so readers learn "
        "what exists."
    )

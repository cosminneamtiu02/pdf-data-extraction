"""Guardrail that .github/CODEOWNERS exists and declares a global default owner.

CODEOWNERS is the mechanical routing signal GitHub uses to populate the
"Reviewers" panel on PRs and — once `require_code_owner_review` is flipped on
the `main-protection` ruleset — to block merges until a code owner has
approved. Issue #411 tracks the absence of this file; this meta-test pins it
so a future refactor cannot silently delete it.

The assertions are intentionally narrow:

- file exists at .github/CODEOWNERS (the only location GitHub reads that is
  not the repo root or docs/; on this repo the .github/ form is canonical
  because it co-locates with dependabot.yml and the workflows directory),
- file is non-empty after stripping comments and whitespace,
- at least one line is a global pattern ``* @<owner>`` that assigns a
  ``@cosmin…``-style owner, matching the PR-authoring rule in CLAUDE.md
  ("all human-authored pull requests on this repo are opened by
  `cosminneamtiu02`").

The global-pattern check deliberately accepts any ``@cosmin…`` prefix so that
renaming the account (unlikely, but cheap to tolerate) does not require
editing this test — only the CODEOWNERS file itself.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
_CODEOWNERS_PATH = _REPO_ROOT / ".github" / "CODEOWNERS"

# Matches a CODEOWNERS global-default line: the literal pattern ``*`` followed
# by at least one owner handle starting with ``@cosmin``. Extra owners on the
# same line (``* @cosmin @other``) are tolerated; comments after ``#`` are
# stripped before matching.
_GLOBAL_COSMIN_OWNER_RE = re.compile(r"^\*\s+.*@cosmin\S*", re.IGNORECASE)


def _non_comment_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        # Strip trailing comments and surrounding whitespace. CODEOWNERS
        # comments are ``#`` to end-of-line; quoting is not supported, so a
        # naive split is safe.
        without_comment = raw.split("#", 1)[0].strip()
        if without_comment:
            lines.append(without_comment)
    return lines


def test_codeowners_file_exists() -> None:
    assert _CODEOWNERS_PATH.is_file(), (
        f"{_CODEOWNERS_PATH} is missing. Issue #411 requires a CODEOWNERS "
        f"file so PRs mechanically surface the right reviewer in GitHub's "
        f"Reviewers panel."
    )


def test_codeowners_has_non_comment_content() -> None:
    text = _CODEOWNERS_PATH.read_text(encoding="utf-8")
    meaningful = _non_comment_lines(text)
    assert meaningful, (
        f"{_CODEOWNERS_PATH} contains only comments/whitespace. A CODEOWNERS "
        f"file with no pattern lines is a no-op and will not populate PR "
        f"reviewers; add at least a ``* @<owner>`` global default."
    )


def test_codeowners_has_cosmin_global_default() -> None:
    text = _CODEOWNERS_PATH.read_text(encoding="utf-8")
    meaningful = _non_comment_lines(text)
    matches = [line for line in meaningful if _GLOBAL_COSMIN_OWNER_RE.match(line)]
    assert matches, (
        f"{_CODEOWNERS_PATH} is missing a ``* @cosmin…`` global default "
        f"line. CLAUDE.md pins cosminneamtiu02 as the PR author for this "
        f"repo (2026-04-20 switch), so the default code owner must match. "
        f"Got non-comment lines: {meaningful!r}"
    )

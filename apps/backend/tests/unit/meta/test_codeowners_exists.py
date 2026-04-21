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

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
_CODEOWNERS_PATH = _REPO_ROOT / ".github" / "CODEOWNERS"


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


def _is_global_cosmin_default(line: str) -> bool:
    """True iff ``line`` is a CODEOWNERS ``*`` pattern whose first owner token
    starts with ``@cosmin`` (case-insensitive).

    CODEOWNERS lines are whitespace-separated: the first token is the file
    pattern, and the remaining tokens are owner handles. Parsing into tokens
    (rather than regex-matching a substring) rejects adversarial cases like
    ``* foo@cosmin.com`` where an email-shaped token would satisfy a naive
    substring match without being a real ``@cosmin…`` owner handle.
    """
    tokens = line.split()
    if len(tokens) < 2:
        return False
    if tokens[0] != "*":
        return False
    return any(token.lower().startswith("@cosmin") for token in tokens[1:])


def _require_codeowners_file() -> Path:
    """Assert the CODEOWNERS file exists, returning its path.

    Each read-based test calls this first so that a missing file surfaces the
    actionable assertion message from this helper rather than a bare
    ``FileNotFoundError`` from ``read_text()``. The dedicated
    ``test_codeowners_file_exists`` test still exists as the canonical
    single-purpose assertion; this helper keeps the other tests robust if
    they are run in isolation or reordered.
    """
    assert _CODEOWNERS_PATH.is_file(), (
        f"{_CODEOWNERS_PATH} is missing. Issue #411 requires a CODEOWNERS "
        f"file so PRs mechanically surface the right reviewer in GitHub's "
        f"Reviewers panel."
    )
    return _CODEOWNERS_PATH


def test_codeowners_file_exists() -> None:
    assert _CODEOWNERS_PATH.is_file(), (
        f"{_CODEOWNERS_PATH} is missing. Issue #411 requires a CODEOWNERS "
        f"file so PRs mechanically surface the right reviewer in GitHub's "
        f"Reviewers panel."
    )


def test_codeowners_has_non_comment_content() -> None:
    path = _require_codeowners_file()
    text = path.read_text(encoding="utf-8")
    meaningful = _non_comment_lines(text)
    assert meaningful, (
        f"{_CODEOWNERS_PATH} contains only comments/whitespace. A CODEOWNERS "
        f"file with no pattern lines is a no-op and will not populate PR "
        f"reviewers; add at least a ``* @<owner>`` global default."
    )


def test_codeowners_has_cosmin_global_default() -> None:
    path = _require_codeowners_file()
    text = path.read_text(encoding="utf-8")
    meaningful = _non_comment_lines(text)
    matches = [line for line in meaningful if _is_global_cosmin_default(line)]
    assert matches, (
        f"{_CODEOWNERS_PATH} is missing a ``* @cosmin…`` global default "
        f"line. CLAUDE.md pins cosminneamtiu02 as the PR author for this "
        f"repo (2026-04-20 switch), so the default code owner must match. "
        f"Got non-comment lines: {meaningful!r}"
    )

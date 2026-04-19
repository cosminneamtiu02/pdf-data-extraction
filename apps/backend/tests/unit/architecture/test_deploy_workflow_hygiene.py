"""Hygiene checks on `.github/workflows/deploy.yml`.

Static assertions about the Deploy workflow — kept here so they run inside
the canonical `task check` gate. Catches drift of the
"do not habituate on-call to a red Deploy job" invariant (#270): until the
container-registry push flow from #121 is wired up, the Deploy job must be
GATED on a repo variable (``DEPLOY_ENABLED``) so GitHub Actions shows it
as **skipped** (neutral) rather than **failed** (red) on every push to main.

When #121 lands, the operator flips the repo variable to ``"true"`` and
the existing job runs. No workflow edit should be required for the flip.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from ._linter_subprocess import REPO_ROOT

_DEPLOY_WORKFLOW: Final[Path] = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

# Matches `exit 1` as a standalone statement — i.e. surrounded by non-word
# characters on both sides so it is robust to punctuation-adjacent forms like
# ``(exit 1)``, ``foo&&exit 1``, or ``exit 1;`` while still excluding
# legitimate neighbours like ``exit 10`` or ``myexit 1``.
_EXIT_ONE_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\w)exit[ \t]+1(?!\w)")

# Characters that put the shell back into "command position" — after any of
# them, an unquoted ``#`` starts a comment. Bash's full grammar also treats
# ``{``, ``!``, ``<``, ``>``, etc. as command-position openers, but the shapes
# that appear in GitHub Actions ``run:`` blocks are well-covered by this set.
_COMMAND_POSITION_TERMINATORS: Final[frozenset[str]] = frozenset(";&|(")


def _load_deploy_workflow() -> dict[str, Any]:
    workflow = yaml.safe_load(_DEPLOY_WORKFLOW.read_text())
    assert isinstance(workflow, dict), (
        f"{_DEPLOY_WORKFLOW} did not parse to a YAML mapping — "
        "got a non-mapping or null value, likely malformed YAML."
    )
    return workflow


def _strip_shell_inline_comment(line: str) -> str:
    """Return `line` with any trailing bash-style inline comment removed.

    Bash treats ``#`` as a comment start only when it is in "command
    position" — at line start, or preceded by whitespace or one of the
    command terminators in :data:`_COMMAND_POSITION_TERMINATORS` — AND
    outside any single- or double-quoted string. Minimal implementation
    that covers the shapes that appear in GitHub Actions ``run:`` blocks,
    so the downstream ``exit 1`` detector does not flag commented-out text
    (``echo foo  # exit 1``, ``echo ok;# exit 1``) as real code.
    """
    in_single = False
    in_double = False
    prev_char = " "  # line start counts as command-position
    for idx, ch in enumerate(line):
        if in_single:
            if ch == "'":
                in_single = False
        elif in_double:
            if ch == '"' and (idx == 0 or line[idx - 1] != "\\"):
                in_double = False
        elif ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "#" and (prev_char.isspace() or prev_char in _COMMAND_POSITION_TERMINATORS):
            return line[:idx].rstrip()
        prev_char = ch
    return line


def test_deploy_job_is_gated_on_deploy_enabled_variable() -> None:
    """The `deploy` job must be gated on the ``DEPLOY_ENABLED`` repo variable.

    Until the rollout story from #121 is wired up, the Deploy workflow has
    nothing meaningful to do — but emitting a red failure on every push to
    main habituates on-call to ignore the Deploy job, which hides real
    failures once the registry push is wired up. The kill switch is at the
    job level (``if: vars.DEPLOY_ENABLED == 'true'``) so GitHub Actions
    renders the whole job as "skipped" (neutral grey) rather than "failed"
    (red) while the variable is unset or anything other than the string
    ``"true"``. Operators flip it to ``"true"`` when the registry path is
    ready; no workflow edit needed.
    """
    workflow = _load_deploy_workflow()
    deploy_job = (workflow.get("jobs") or {}).get("deploy")

    assert deploy_job is not None, (
        "deploy.yml must define a `deploy` job — the kill-switch gate "
        "cannot be enforced against a missing job."
    )

    assert isinstance(deploy_job, dict), (
        f"`deploy` job must be a YAML mapping; got {type(deploy_job).__name__}."
    )

    guard = deploy_job.get("if")
    assert guard == "vars.DEPLOY_ENABLED == 'true'", (
        "The `deploy` job must carry `if: vars.DEPLOY_ENABLED == 'true'` "
        "so it is SKIPPED (neutral) rather than FAILED (red) on every push "
        "to main until the registry-push path from #121 is wired up. "
        f"Got: {guard!r}"
    )


def test_deploy_job_has_no_unconditional_exit_one() -> None:
    """No step in the `deploy` job may carry a raw `exit 1`.

    The pre-#270 workflow shipped a ``Push to registry`` step whose body
    was ``echo "::error::..."; exit 1`` — a permanent red annotation that
    fired on every push to main. Gating the job at the ``if:`` level is
    not enough on its own: once the variable flips to ``"true"`` and the
    job runs, a forgotten ``exit 1`` in the body would reintroduce the
    red. Keep the body a descriptive placeholder that exits cleanly until
    the real registry push replaces it.
    """
    workflow = _load_deploy_workflow()
    deploy_job = (workflow.get("jobs") or {}).get("deploy")
    assert isinstance(deploy_job, dict), "deploy job missing or malformed."

    offenders: list[str] = []
    for step in deploy_job.get("steps") or []:
        run_block = step.get("run")
        if not isinstance(run_block, str):
            continue
        for raw_line in run_block.splitlines():
            line = _strip_shell_inline_comment(raw_line.strip())
            if not line or line.startswith("#"):
                continue
            if _EXIT_ONE_RE.search(line):
                offenders.append(
                    f"step '{step.get('name', '<unnamed>')}' contains `exit 1`: {line!r}",
                )

    assert not offenders, (
        "deploy.yml `deploy` job steps must not unconditionally `exit 1` — "
        "that reintroduces the permanent-red regression tracked in #270:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # Plain inline comment after code — stripped.
        ("echo foo  # exit 1", "echo foo"),
        # No comment — line returned unchanged.
        ("echo foo", "echo foo"),
        # `#` embedded inside double-quoted string is not a comment.
        ('echo "foo # bar"', 'echo "foo # bar"'),
        # `#` embedded inside single-quoted string is not a comment.
        ("echo 'foo # bar'", "echo 'foo # bar'"),
        # `#` not preceded by whitespace (e.g. inside a URL fragment) is
        # not a comment start.
        ("curl https://example.com/path#frag", "curl https://example.com/path#frag"),
        # Real `exit 1` followed by a comment — the code survives.
        ("exit 1 # intentional failure", "exit 1"),
        # Full-line comment — returned as empty so the outer `startswith('#')`
        # guard can drop it. (The strip removes the comment; nothing remains.)
        ("# exit 1", ""),
        # Command-position `#` after `;` (no whitespace) — bash comment start.
        ("echo ok;# exit 1", "echo ok;"),
        # Command-position `#` after `&&` — bash comment start.
        ("echo ok&&# exit 1", "echo ok&&"),
        # Command-position `#` after `||` — bash comment start.
        ("echo ok||# exit 1", "echo ok||"),
        # Command-position `#` immediately after subshell `(`.
        ("(# inside subshell", "("),
    ],
)
def test_strip_shell_inline_comment(line: str, expected: str) -> None:
    assert _strip_shell_inline_comment(line) == expected


@pytest.mark.parametrize(
    ("line", "should_match"),
    [
        # Bare statement.
        ("exit 1", True),
        # Trailing semicolon.
        ("exit 1;", True),
        # Subshell form — tokenizer would miss this.
        ("(exit 1)", True),
        # Punctuation-adjacent `&&` with no spaces — tokenizer would miss.
        ("foo&&exit 1", True),
        # `||` fallthrough — tokenizer would miss without `||` splitting.
        ("cmd||exit 1", True),
        # Leading whitespace is fine.
        ("    exit 1", True),
        # `exit 10` must NOT match — we only object to exit-code 1.
        ("exit 10", False),
        # `exit 11` likewise.
        ("exit 11", False),
        # `myexit 1` (e.g. a custom function) must NOT match.
        ("myexit 1", False),
        # `exit 1a` — suffix prevents match.
        ("exit 1a", False),
        # Bare `exit` (no code) is benign.
        ("exit", False),
    ],
)
def test_exit_one_regex(
    line: str,
    should_match: bool,  # noqa: FBT001 -- parametrized expected, not a flag argument
) -> None:
    assert bool(_EXIT_ONE_RE.search(line)) is should_match

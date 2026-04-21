"""Meta-tests for YAML/workflow/Dockerfile lint hooks in `.pre-commit-config.yaml`.

Issue #359: `check-yaml` was excluding `Taskfile.yml` for historical reasons
(the Taskfile is plain YAML — `version: '3'` — so the exclusion was leftover,
not load-bearing). The issue also flagged that `actionlint` (GitHub Actions
workflows), `hadolint` (Dockerfile), and `yamllint` were not wired into
pre-commit at all, which is a static-analysis hygiene gap. `actionlint` in
particular is what catches the `github.actor` vs
`github.event.pull_request.user.login` class of auto-merge-guard bug
statically, before it ships.

This module parses `.pre-commit-config.yaml` and asserts:

- The three hooks (actionlint, hadolint, yamllint) are registered against
  the expected upstream hook repos AND expose the expected hook `id`. Both
  checks are load-bearing: a hook can have the right repo URL but the wrong
  (or missing) hook id and still false-pass a repo-only probe.
- `Taskfile.yml` is NOT matched by any hook-level, repo-level, or top-level
  `exclude` regex (closing the original regression: if a future edit re-adds
  the exclusion — even via a pattern different from the original
  `^Taskfile\\.yml$` — this test fails loudly with a pointer back to #359).
- A `.yamllint` config file exists at the repo root so the `yamllint` hook
  has deterministic rules (without it, yamllint applies its "default" preset,
  which clashes with the repo's 2-space-indented YAML style).

The tests are deliberately structural — they assert hook registration, not
hook behaviour. Hook behaviour is exercised by `pre-commit run --all-files`
in CI / local `task check`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_PRECOMMIT_PATH: Final[Path] = _REPO_ROOT / ".pre-commit-config.yaml"
_YAMLLINT_CONFIG_PATH: Final[Path] = _REPO_ROOT / ".yamllint"

# The literal path we test every `exclude` regex against. pre-commit matches
# `exclude` patterns with `re.search` against forward-slash-normalised file
# paths, so the literal `Taskfile.yml` is what the regexes will actually see
# at hook runtime. Evaluating the real patterns against this literal catches
# any equivalent spelling (`^Task.*\.yml$`, `Task.*file`, `.*\.ya?ml$`, …)
# without forcing the test to enumerate every possible rewrite.
_TASKFILE_LITERAL_PATH: Final[str] = "Taskfile.yml"

_ACTIONLINT_REPO_SUBSTRING: Final[str] = "rhysd/actionlint"
_HADOLINT_REPO_SUBSTRING: Final[str] = "hadolint-py"
_YAMLLINT_REPO_SUBSTRING: Final[str] = "adrienverge/yamllint"

_ACTIONLINT_HOOK_ID: Final[str] = "actionlint"
_HADOLINT_HOOK_ID: Final[str] = "hadolint"
_YAMLLINT_HOOK_ID: Final[str] = "yamllint"


def _load_precommit_config() -> dict[str, Any]:
    data = yaml.safe_load(_PRECOMMIT_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = (
            f"{_PRECOMMIT_PATH} did not parse to a mapping "
            f"(got {type(data).__name__}); pre-commit schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    return data


def _iter_repos(config: dict[str, Any]) -> list[dict[str, Any]]:
    repos = config.get("repos", [])
    if not isinstance(repos, list):
        msg = (
            f"{_PRECOMMIT_PATH} top-level 'repos' is {type(repos).__name__!r} "
            f"(expected list); pre-commit schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    return [r for r in repos if isinstance(r, dict)]


def _iter_hooks(config: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    hooks_with_repos: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for repo in _iter_repos(config):
        hooks = repo.get("hooks", [])
        if not isinstance(hooks, list):
            repo_name = repo.get("repo")
            msg = (
                f"{_PRECOMMIT_PATH} repo {repo_name!r} has 'hooks' as "
                f"{type(hooks).__name__!r} (expected list); pre-commit "
                f"schema may have changed."
            )
            raise AssertionError(msg)  # noqa: TRY004
        hooks_with_repos.extend((repo, hook) for hook in hooks if isinstance(hook, dict))
    return hooks_with_repos


def _repo_with_hook_id_registered(repo_substring: str, hook_id: str) -> bool:
    """Return True iff some repo whose URL contains `repo_substring` declares `hook_id`.

    Requiring BOTH the repo URL and the hook id guards against two failure
    modes a repo-only probe would miss: (1) a repo block kept with empty
    `hooks:`, and (2) a hook id typo / rename under the right repo. The
    invariant we care about is "the hook is actually wired to run," which
    means both identifiers must line up.
    """
    config = _load_precommit_config()
    for repo, hook in _iter_hooks(config):
        url = repo.get("repo")
        if not isinstance(url, str) or repo_substring not in url:
            continue
        if hook.get("id") == hook_id:
            return True
    return False


def test_actionlint_hook_is_configured() -> None:
    """actionlint is wired in via the official `rhysd/actionlint` hook repo.

    actionlint statically validates every file under `.github/workflows/`
    and catches a class of bugs that would otherwise only surface at
    runtime (e.g. `github.actor` read where `github.event.pull_request.user.login`
    was intended — see CLAUDE.md's Dependabot section for the incident
    this guards against).
    """
    assert _repo_with_hook_id_registered(_ACTIONLINT_REPO_SUBSTRING, _ACTIONLINT_HOOK_ID), (
        "actionlint hook not registered in .pre-commit-config.yaml. "
        "Add a block pointing at https://github.com/rhysd/actionlint "
        f"with the `{_ACTIONLINT_HOOK_ID}` hook id. See issue #359."
    )


def test_hadolint_hook_is_configured() -> None:
    """hadolint is wired in via `AleksaC/hadolint-py`.

    The `AleksaC/hadolint-py` mirror is used (not `hadolint/hadolint`)
    because the upstream hadolint project ships only a Docker-based
    pre-commit hook, while `hadolint-py` packages the binary as a Python
    wheel — matching the project's "no Docker in local dev loop" default.
    """
    assert _repo_with_hook_id_registered(_HADOLINT_REPO_SUBSTRING, _HADOLINT_HOOK_ID), (
        "hadolint hook not registered in .pre-commit-config.yaml. "
        "Add a block pointing at https://github.com/AleksaC/hadolint-py "
        f"with the `{_HADOLINT_HOOK_ID}` hook id. See issue #359."
    )


def test_yamllint_hook_is_configured() -> None:
    """yamllint is wired in via the official `adrienverge/yamllint` hook repo.

    yamllint catches YAML style drift the `check-yaml` pre-commit hook
    doesn't: indentation, trailing whitespace in multi-line strings,
    duplicate keys at non-top levels, etc.
    """
    assert _repo_with_hook_id_registered(_YAMLLINT_REPO_SUBSTRING, _YAMLLINT_HOOK_ID), (
        "yamllint hook not registered in .pre-commit-config.yaml. "
        "Add a block pointing at https://github.com/adrienverge/yamllint "
        f"with the `{_YAMLLINT_HOOK_ID}` hook id. See issue #359."
    )


def test_yamllint_config_file_exists() -> None:
    """A `.yamllint` config file pins the yamllint ruleset explicitly.

    yamllint's zero-config default preset assumes 2-space indentation but
    warns on several rules the repo has no quarrel with (e.g. `comments`
    spacing, `document-start` marker). Without a config file, every new
    YAML contributor fights the defaults. Pinning the rules in
    `.yamllint` makes the style contract explicit.
    """
    assert _YAMLLINT_CONFIG_PATH.is_file(), (
        f"{_YAMLLINT_CONFIG_PATH} is missing. yamllint will fall back to "
        "its default preset, which is stricter than this repo's YAML "
        "style. Create a minimal `.yamllint` that extends the default "
        "and relaxes the rules that do not match repo conventions."
    )


def _exclude_matches_taskfile(exclude: object) -> bool:
    """Return True iff `exclude` is a string regex that matches `Taskfile.yml`.

    pre-commit applies `exclude` regexes via `re.search` against the
    forward-slash-normalised path of each candidate file, so evaluating the
    actual regex against the literal path is the load-bearing check — not
    pattern-string equality. A malformed regex is treated as "does not
    match" (pre-commit itself would raise at load time, so the repo cannot
    reach a state where a malformed exclude hides the Taskfile).
    """
    if not isinstance(exclude, str) or not exclude:
        return False
    try:
        return re.search(exclude, _TASKFILE_LITERAL_PATH) is not None
    except re.error:
        return False


def test_taskfile_is_not_excluded_from_any_hook() -> None:
    """Taskfile.yml must not be matched by any pre-commit exclude pattern.

    The original `check-yaml: exclude: ^Taskfile\\.yml$` entry was a
    historical leftover from when the Taskfile was suspected of
    containing YAML-unsafe go-template expressions. In practice the
    Taskfile parses cleanly with PyYAML (go-template expressions only
    live inside string values, and `{{` has no YAML-structural meaning
    there). Removing the exclusion was the core fix for #359.

    This test enforces that no future edit silently re-excludes the
    Taskfile from any hook. It evaluates every `exclude` regex (at the
    hook, repo, and top-level positions) against the literal path
    `Taskfile.yml`, which catches any equivalent pattern — not just the
    specific `^Taskfile\\.yml$` that was originally removed. If a hook
    genuinely cannot process the Taskfile, add a targeted carve-out
    (hook-id-scoped) with a comment rather than silently re-adding a
    broad exclude.
    """
    config = _load_precommit_config()

    offenders: list[str] = []

    top_level_exclude = config.get("exclude")
    if _exclude_matches_taskfile(top_level_exclude):
        offenders.append(
            f"top-level exclude={top_level_exclude!r} matches {_TASKFILE_LITERAL_PATH!r}",
        )

    # pre-commit's documented schema has no `repo`-level `exclude` key, but a
    # future schema extension or a typo (someone meaning to put `exclude`
    # under a hook placing it under the repo block instead) would be
    # invisible to a hook-only scan. Treat any unexpected `exclude` on a
    # repo block as an offender too — either it matches and we catch the
    # regression, or it doesn't match and we stay silent.
    for repo in _iter_repos(config):
        repo_exclude = repo.get("exclude")
        if _exclude_matches_taskfile(repo_exclude):
            repo_url = repo.get("repo", "<unknown repo>")
            offenders.append(
                f"repo '{repo_url}' exclude={repo_exclude!r} matches {_TASKFILE_LITERAL_PATH!r}",
            )

    for repo, hook in _iter_hooks(config):
        exclude = hook.get("exclude")
        if _exclude_matches_taskfile(exclude):
            hook_id = hook.get("id", "<unnamed>")
            repo_url = repo.get("repo", "<unknown repo>")
            offenders.append(
                f"hook '{hook_id}' from repo '{repo_url}' "
                f"has exclude={exclude!r}, which matches "
                f"{_TASKFILE_LITERAL_PATH!r}",
            )

    assert not offenders, (
        f"{_TASKFILE_LITERAL_PATH} is excluded from pre-commit hook(s). "
        "This was the regression fixed by issue #359 — the Taskfile "
        "parses as valid YAML, so `check-yaml` and other hooks should "
        "cover it.\nOffenders:\n" + "\n".join(f"  - {o}" for o in offenders)
    )

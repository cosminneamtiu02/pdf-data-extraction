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
  the expected upstream hook repos.
- `Taskfile.yml` is NOT listed in any hook's `exclude` pattern (closing the
  original regression: if a future edit re-adds the exclusion, this test
  fails loudly with a pointer back to #359).
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

_TASKFILE_EXCLUDE_PATTERNS: Final[tuple[str, ...]] = (
    r"^Taskfile\.yml$",
    r"Taskfile\.yml",
    r"^Taskfile\.ya?ml$",
)

_ACTIONLINT_REPO_SUBSTRING: Final[str] = "rhysd/actionlint"
_HADOLINT_REPO_SUBSTRING: Final[str] = "hadolint-py"
_YAMLLINT_REPO_SUBSTRING: Final[str] = "adrienverge/yamllint"


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
    return [
        (repo, hook)
        for repo in _iter_repos(config)
        for hook in repo.get("hooks") or []
        if isinstance(hook, dict)
    ]


def _repo_registered(substring: str) -> bool:
    config = _load_precommit_config()
    for repo in _iter_repos(config):
        url = repo.get("repo")
        if isinstance(url, str) and substring in url:
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
    assert _repo_registered(_ACTIONLINT_REPO_SUBSTRING), (
        "actionlint hook not registered in .pre-commit-config.yaml. "
        "Add a block pointing at https://github.com/rhysd/actionlint "
        "with the `actionlint` hook id. See issue #359."
    )


def test_hadolint_hook_is_configured() -> None:
    """hadolint is wired in via `AleksaC/hadolint-py`.

    The `AleksaC/hadolint-py` mirror is used (not `hadolint/hadolint`)
    because the upstream hadolint project ships only a Docker-based
    pre-commit hook, while `hadolint-py` packages the binary as a Python
    wheel — matching the project's "no Docker in local dev loop" default.
    """
    assert _repo_registered(_HADOLINT_REPO_SUBSTRING), (
        "hadolint hook not registered in .pre-commit-config.yaml. "
        "Add a block pointing at https://github.com/AleksaC/hadolint-py "
        "with the `hadolint` hook id. See issue #359."
    )


def test_yamllint_hook_is_configured() -> None:
    """yamllint is wired in via the official `adrienverge/yamllint` hook repo.

    yamllint catches YAML style drift the `check-yaml` pre-commit hook
    doesn't: indentation, trailing whitespace in multi-line strings,
    duplicate keys at non-top levels, etc.
    """
    assert _repo_registered(_YAMLLINT_REPO_SUBSTRING), (
        "yamllint hook not registered in .pre-commit-config.yaml. "
        "Add a block pointing at https://github.com/adrienverge/yamllint "
        "with the `yamllint` hook id. See issue #359."
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


def test_taskfile_is_not_excluded_from_any_hook() -> None:
    """Taskfile.yml must not be excluded from any pre-commit hook.

    The original `check-yaml: exclude: ^Taskfile\\.yml$` entry was a
    historical leftover from when the Taskfile was suspected of
    containing YAML-unsafe go-template expressions. In practice the
    Taskfile parses cleanly with PyYAML (go-template expressions only
    live inside string values, and `{{` has no YAML-structural meaning
    there). Removing the exclusion was the core fix for #359.

    This test enforces that no future edit silently re-excludes the
    Taskfile from any hook. If a hook genuinely cannot process the
    Taskfile, document the reason in this test (add a `match.group(0)
    == "EXPECTED_PATTERN"` carve-out with a comment) rather than
    silently re-adding a broad exclude.
    """
    config = _load_precommit_config()

    offenders: list[str] = []
    for repo, hook in _iter_hooks(config):
        exclude = hook.get("exclude")
        if not isinstance(exclude, str):
            continue
        for pattern in _TASKFILE_EXCLUDE_PATTERNS:
            if exclude == pattern or re.search(pattern, exclude):
                hook_id = hook.get("id", "<unnamed>")
                repo_url = repo.get("repo", "<unknown repo>")
                offenders.append(
                    f"hook '{hook_id}' from repo '{repo_url}' "
                    f"has exclude={exclude!r}, which matches Taskfile.yml",
                )
                break

    assert not offenders, (
        "Taskfile.yml is excluded from pre-commit hook(s). "
        "This was the regression fixed by issue #359 — the Taskfile "
        "parses as valid YAML, so `check-yaml` and other hooks should "
        "cover it.\nOffenders:\n" + "\n".join(f"  - {o}" for o in offenders)
    )

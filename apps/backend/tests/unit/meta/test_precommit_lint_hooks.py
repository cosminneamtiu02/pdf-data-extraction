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
- A `.hadolint.yaml` config file exists at the repo root so the `hadolint`
  hook's `--config .hadolint.yaml` argument resolves at runtime (closing the
  PR #434 review gap — the hook is wired to read the file, so its absence
  should be a test failure rather than a runtime hook-load error).

The tests are deliberately structural — they assert hook registration, not
hook behaviour. To validate hook behaviour, run `pre-commit run --all-files`
separately; `task check` does not invoke pre-commit (by design — pre-commit
owns the git-hook lifecycle, `task check` owns the single-command gate).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_PRECOMMIT_PATH: Final[Path] = _REPO_ROOT / ".pre-commit-config.yaml"
_YAMLLINT_CONFIG_PATH: Final[Path] = _REPO_ROOT / ".yamllint"
_HADOLINT_CONFIG_PATH: Final[Path] = _REPO_ROOT / ".hadolint.yaml"

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
        if not isinstance(url, str):
            if "repo" in repo:
                msg = (
                    f"{_PRECOMMIT_PATH} repo entry has 'repo' as "
                    f"{type(url).__name__!r} (expected str); pre-commit "
                    f"schema may have changed."
                )
                raise AssertionError(msg)
            continue
        if repo_substring not in url:
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


def test_hadolint_config_file_exists() -> None:
    """A `.hadolint.yaml` config file pins the hadolint ruleset explicitly.

    The hadolint hook is wired with `args: ["--config", ".hadolint.yaml"]`
    in `.pre-commit-config.yaml`; if the config file is removed or renamed,
    pre-commit fails at hook runtime with a "config not found" error rather
    than a clear test failure. Paralleling `test_yamllint_config_file_exists`,
    this meta-test pins the invariant that `.hadolint.yaml` exists at the
    repo root so the hook's `--config` argument resolves and the
    DL3008-ignored-by-design rationale (digest-pinned base image, see
    PR #431 / issue #362) stays in force.
    """
    assert _HADOLINT_CONFIG_PATH.is_file(), (
        f"{_HADOLINT_CONFIG_PATH} is missing. The hadolint hook in "
        ".pre-commit-config.yaml references it via `--config .hadolint.yaml`, "
        "so pre-commit will fail at runtime without it. Restore the file "
        "(or update the hook's `args` to point at the new location)."
    )


def _exclude_matches_taskfile(exclude: object) -> bool:
    """Return True iff `exclude` is a string regex that matches `Taskfile.yml`.

    pre-commit applies `exclude` regexes via `re.search` against the
    forward-slash-normalised path of each candidate file, so evaluating the
    actual regex against the literal path is the load-bearing check — not
    pattern-string equality. A malformed regex is treated as a test
    failure, mirroring pre-commit's load-time behaviour: pre-commit itself
    would raise on load, so any `re.error` here means the repo is shipping
    a broken `.pre-commit-config.yaml` and this meta-test must catch it
    loudly instead of silently masking the Taskfile exclude by returning
    False.
    """
    if not isinstance(exclude, str) or not exclude:
        return False
    try:
        return re.search(exclude, _TASKFILE_LITERAL_PATH) is not None
    except re.error as exc:
        msg = (
            f"Invalid pre-commit exclude regex {exclude!r} in "
            f"{_PRECOMMIT_PATH}: {exc}. pre-commit would reject this at "
            "load time; fix the regex rather than letting this meta-test "
            "ignore it."
        )
        raise AssertionError(msg) from exc


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


def test_exclude_matches_taskfile_rejects_malformed_regex() -> None:
    """A malformed `exclude` regex must fail this meta-test loudly.

    Regression guard for the Copilot review on PR #434: the earlier
    implementation swallowed `re.error` and returned False, which would
    let a broken `.pre-commit-config.yaml` (malformed `exclude`) pass
    this meta-test silently even though pre-commit itself would reject
    the config at load time. The helper now re-raises as AssertionError;
    this test pins that behaviour so a future revert cannot reintroduce
    the silent-swallow failure mode.
    """
    # Unbalanced parenthesis: `re.compile` raises `re.error`, so any future
    # attempt to "fix" the helper by suppressing the error would show up
    # here instead of silently passing. We avoid `pytest.raises` in this
    # file because the pre-push pyright hook runs from the repo root and
    # cannot resolve `import pytest` without the apps/backend venv on
    # its path — using a try/except keeps this meta-test independent of
    # that pre-push-hook quirk.
    malformed_regex = "Task(file"
    raised_expected_assertion = False
    try:
        _exclude_matches_taskfile(malformed_regex)
    except AssertionError as exc:
        if "Invalid pre-commit exclude regex" not in str(exc):
            msg = (
                f"AssertionError raised but message did not mention the "
                f"malformed-regex invariant: {exc!r}"
            )
            raise AssertionError(msg) from exc
        raised_expected_assertion = True
    assert raised_expected_assertion, (
        "_exclude_matches_taskfile silently accepted a malformed regex "
        f"{malformed_regex!r}; the helper must raise AssertionError so "
        "a broken .pre-commit-config.yaml is surfaced by `task check`."
    )

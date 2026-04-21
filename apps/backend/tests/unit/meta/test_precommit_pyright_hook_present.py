"""Guardrail: `.pre-commit-config.yaml` must include a pyright hook pinned
to the same version as the one resolved in `apps/backend/uv.lock`.

Why:

- Issue #360: the repo historically had ruff drift guard but no pyright
  guard. Pyright was absent from pre-commit altogether, so CI could fail
  for type errors that `task check:types` catches but no pre-commit stage
  surfaces. Adding a pyright hook closes the gap.
- The hook rev must track `uv.lock`'s `pyright` package version (which is
  the source of truth for this repo — same convention as the ruff parity
  test in `test_precommit_ruff_pin_matches_lockfile.py`).

When this test fails, pick one of:

- The `.pre-commit-config.yaml` has no `RobertCraigie/pyright-python`
  entry — add one with `rev: vX.Y.Z` matching `uv.lock`.
- The rev drifted from `uv.lock` — bump the pre-commit `rev:` to match,
  OR bump pyright in `apps/backend/pyproject.toml` and re-run `uv lock`.

Editing the pre-commit `rev:` alone will NOT change `apps/backend/uv.lock`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[5]
_PRECOMMIT_PATH = _REPO_ROOT / ".pre-commit-config.yaml"
_UV_LOCK_PATH = _REPO_ROOT / "apps" / "backend" / "uv.lock"

_PYRIGHT_PRECOMMIT_REPO_SUBSTRING = "RobertCraigie/pyright-python"
_PYRIGHT_HOOK_ID = "pyright"
_PYRIGHT_PACKAGE_NAME = "pyright"


def _precommit_pyright_entry() -> dict[str, object]:
    """Return the pre-commit repo dict that hosts the pyright hook.

    AssertionError (not TypeError) is the intended failure shape: this is a
    pytest guardrail helper, and pytest's test-runner messaging is keyed on
    AssertionError for test-assertion-style reporting. The ``noqa: TRY004``
    suppressions in this file are scoped to ``raise AssertionError`` sites
    that directly follow ``isinstance(...)`` checks, because those are the
    cases ruff flags for TRY004. Other ``raise AssertionError`` lines in
    these helpers (missing ``rev``/``version`` values, repo-not-found, etc.)
    follow None/value checks and do NOT trigger TRY004, so applying the
    suppression universally would surface as RUF100 (unused-noqa).
    """
    data = yaml.safe_load(_PRECOMMIT_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = (
            f"{_PRECOMMIT_PATH} did not parse to a mapping "
            f"(got {type(data).__name__}); pre-commit config schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    repos = data.get("repos", [])
    if not isinstance(repos, list):
        msg = (
            f"{_PRECOMMIT_PATH} top-level 'repos' is {type(repos).__name__!r} "
            f"(expected list); pre-commit config schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    for repo in repos:
        if not isinstance(repo, dict):
            continue
        repo_url = repo.get("repo")
        if repo_url is None:
            continue
        if not isinstance(repo_url, str):
            msg = (
                f"pre-commit repo entry in {_PRECOMMIT_PATH} has non-string 'repo' "
                f"(got {type(repo_url).__name__!r} in {repo!r}); pre-commit config "
                f"schema may have changed."
            )
            raise AssertionError(msg)  # noqa: TRY004
        if _PYRIGHT_PRECOMMIT_REPO_SUBSTRING in repo_url:
            return repo
    msg = (
        f"{_PYRIGHT_PRECOMMIT_REPO_SUBSTRING} repo not found in {_PRECOMMIT_PATH}; "
        f"add a pre-commit entry for pyright-python (issue #360)."
    )
    raise AssertionError(msg)


def _precommit_pyright_rev() -> str:
    repo = _precommit_pyright_entry()
    rev = repo.get("rev")
    if rev is None:
        msg = (
            f"pyright-python entry in {_PRECOMMIT_PATH} is missing 'rev' "
            f"(got {repo!r}); pre-commit config schema may have changed."
        )
        raise AssertionError(msg)
    return str(rev).removeprefix("v")


def _precommit_pyright_hook_ids() -> list[str]:
    repo = _precommit_pyright_entry()
    hooks = repo.get("hooks", [])
    if not isinstance(hooks, list):
        msg = (
            f"pyright-python entry in {_PRECOMMIT_PATH} has non-list 'hooks' "
            f"(got {type(hooks).__name__!r}); pre-commit config schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    ids: list[str] = []
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        hook_id = hook.get("id")
        if isinstance(hook_id, str):
            ids.append(hook_id)
    return ids


def _uv_lock_pyright_version() -> str:
    data = tomllib.loads(_UV_LOCK_PATH.read_text(encoding="utf-8"))
    packages = data.get("package", [])
    if not isinstance(packages, list):
        msg = (
            f"{_UV_LOCK_PATH} top-level 'package' is {type(packages).__name__!r} "
            f"(expected list); uv.lock schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    for package in packages:
        if not isinstance(package, dict):
            continue
        if package.get("name") != _PYRIGHT_PACKAGE_NAME:
            continue
        version = package.get("version")
        if version is None:
            msg = (
                f"pyright package entry in {_UV_LOCK_PATH} is missing 'version' "
                f"(got {package!r}); uv.lock schema may have changed."
            )
            raise AssertionError(msg)
        return str(version)
    msg = f"pyright not found in {_UV_LOCK_PATH}"
    raise AssertionError(msg)


def test_precommit_has_pyright_hook() -> None:
    ids = _precommit_pyright_hook_ids()
    missing_msg = (
        f"pyright-python entry in {_PRECOMMIT_PATH} does not register hook "
        f"{_PYRIGHT_HOOK_ID!r} (got hooks={ids!r}). Add "
        f"`- id: {_PYRIGHT_HOOK_ID}` under the pyright-python repo entry."
    )
    assert _PYRIGHT_HOOK_ID in ids, missing_msg


def test_precommit_pyright_rev_matches_uv_lock_version() -> None:
    rev = _precommit_pyright_rev()
    locked = _uv_lock_pyright_version()
    drift_msg = (
        f"pyright version drift: .pre-commit-config.yaml rev='v{rev}' "
        f"but uv.lock version={locked!r}. Bump the pyright-python rev "
        f"in .pre-commit-config.yaml to 'v{locked}'."
    )
    assert rev == locked, drift_msg

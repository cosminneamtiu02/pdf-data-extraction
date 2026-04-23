"""Parity guardrail between .pre-commit-config.yaml and uv.lock for ruff.

When Dependabot bumps ruff in pyproject.toml, uv.lock also advances but the
pre-commit hook rev stays pinned at the older tag. The hooks then produce
different lint results from CI. This test asserts they stay in sync.

When this test fails, pick one of:

- Bump the `rev:` under the `ruff-pre-commit` repo in
  .pre-commit-config.yaml to match the resolved ruff version in uv.lock
  (use this path when uv.lock / apps/backend/pyproject.toml are the
  source of truth, which is the default for this repo); OR
- If you intended to bump ruff via pre-commit-config, bump ruff in
  apps/backend/pyproject.toml and re-run `uv lock` so uv.lock picks up
  the new resolved version.

Editing the pre-commit `rev:` alone will NOT change `apps/backend/uv.lock`.
"""

from __future__ import annotations

import tomllib

import yaml

from tests._paths import BACKEND_DIR as _BACKEND_DIR
from tests._paths import REPO_ROOT as _REPO_ROOT

_PRECOMMIT_PATH = _REPO_ROOT / ".pre-commit-config.yaml"
_UV_LOCK_PATH = _BACKEND_DIR / "uv.lock"

_RUFF_PRECOMMIT_REPO_SUBSTRING = "ruff-pre-commit"
_RUFF_PACKAGE_NAME = "ruff"


def _precommit_ruff_rev() -> str:
    data = yaml.safe_load(_PRECOMMIT_PATH.read_text(encoding="utf-8"))
    # AssertionError (not TypeError) is the intended failure shape: this is a
    # pytest guardrail helper, and pytest's test-runner messaging is keyed on
    # AssertionError for test-assertion-style reporting. The ``noqa: TRY004``
    # suppressions in this file are scoped to ``raise AssertionError`` sites
    # that directly follow ``isinstance(...)`` checks, because those are the
    # cases ruff flags for TRY004. Other ``raise AssertionError`` lines in
    # these helpers (missing ``rev``/``version`` values, repo-not-found,
    # etc.) follow None/value checks and do NOT trigger TRY004, so applying
    # the suppression universally would surface as RUF100 (unused-noqa).
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
        if _RUFF_PRECOMMIT_REPO_SUBSTRING not in repo_url:
            continue
        rev = repo.get("rev")
        if rev is None:
            msg = (
                f"ruff-pre-commit entry in {_PRECOMMIT_PATH} is missing 'rev' "
                f"(got {repo!r}); pre-commit config schema may have changed."
            )
            raise AssertionError(msg)
        return str(rev).removeprefix("v")
    msg = f"{_RUFF_PRECOMMIT_REPO_SUBSTRING} repo not found in {_PRECOMMIT_PATH}"
    raise AssertionError(msg)


def _uv_lock_ruff_version() -> str:
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
        if package.get("name") != _RUFF_PACKAGE_NAME:
            continue
        version = package.get("version")
        if version is None:
            msg = (
                f"ruff package entry in {_UV_LOCK_PATH} is missing 'version' "
                f"(got {package!r}); uv.lock schema may have changed."
            )
            raise AssertionError(msg)
        return str(version)
    msg = f"ruff not found in {_UV_LOCK_PATH}"
    raise AssertionError(msg)


def test_precommit_ruff_rev_matches_uv_lock_version() -> None:
    rev = _precommit_ruff_rev()
    locked = _uv_lock_ruff_version()
    drift_msg = (
        f"ruff version drift: .pre-commit-config.yaml rev='v{rev}' "
        f"but uv.lock version={locked!r}. Bump the ruff-pre-commit rev "
        f"in .pre-commit-config.yaml to 'v{locked}'."
    )
    assert rev == locked, drift_msg

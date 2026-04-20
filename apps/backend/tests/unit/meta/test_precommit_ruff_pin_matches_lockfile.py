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
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[5]
_PRECOMMIT_PATH = _REPO_ROOT / ".pre-commit-config.yaml"
_UV_LOCK_PATH = _REPO_ROOT / "apps" / "backend" / "uv.lock"

_RUFF_PRECOMMIT_REPO_SUBSTRING = "ruff-pre-commit"
_RUFF_PACKAGE_NAME = "ruff"


def _precommit_ruff_rev() -> str:
    data = yaml.safe_load(_PRECOMMIT_PATH.read_text(encoding="utf-8"))
    # AssertionError (not TypeError) is the intended failure shape: this is a
    # pytest guardrail helper, and pytest's test-runner messaging is keyed on
    # AssertionError for test-assertion-style reporting.
    if not isinstance(data, dict):
        msg = (
            f"{_PRECOMMIT_PATH} did not parse to a mapping "
            f"(got {type(data).__name__}); pre-commit config schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    for repo in data.get("repos", []):
        if not isinstance(repo, dict):
            continue
        repo_url = repo.get("repo")
        if repo_url is None or _RUFF_PRECOMMIT_REPO_SUBSTRING not in repo_url:
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
    for package in data.get("package", []):
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

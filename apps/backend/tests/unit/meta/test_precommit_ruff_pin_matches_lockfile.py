"""Parity guardrail between .pre-commit-config.yaml and uv.lock for ruff.

When Dependabot bumps ruff in pyproject.toml, uv.lock also advances but the
pre-commit hook rev stays pinned at the older tag. The hooks then produce
different lint results from CI. This test asserts they stay in sync.

When this test fails, bump the `rev:` under the `ruff-pre-commit` repo in
.pre-commit-config.yaml to match the resolved ruff version in uv.lock (or
re-run `uv lock` if you meant to bump ruff via pre-commit-config).
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
    for repo in data["repos"]:
        if _RUFF_PRECOMMIT_REPO_SUBSTRING in repo["repo"]:
            return str(repo["rev"]).lstrip("v")
    msg = f"{_RUFF_PRECOMMIT_REPO_SUBSTRING} repo not found in {_PRECOMMIT_PATH}"
    raise AssertionError(msg)


def _uv_lock_ruff_version() -> str:
    with _UV_LOCK_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    for package in data["package"]:
        if package["name"] == _RUFF_PACKAGE_NAME:
            return str(package["version"])
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

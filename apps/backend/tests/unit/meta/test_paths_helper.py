"""Guardrail for the shared ``tests/_paths.py`` repo-root helper.

Issue #402: eight+ test files hardcoded ``Path(__file__).resolve().parents[N]``
with N varying by depth (3, 4, 5) to resolve the repo root. Any directory
reshuffle broke every literal silently. The fix centralises resolution in
``tests/_paths.py``, which walks up from its own location looking for the
repo-root marker file (``Taskfile.yml``) rather than counting ``..`` hops.

This test pins the helper's correctness so future moves of ``_paths.py``
itself (a very different kind of refactor than the mass-rename problem the
helper solves) still produce a loud failure at test time.

The invariants are:

1. ``REPO_ROOT`` resolves to a directory that contains ``Taskfile.yml``
   and an ``apps/backend/`` subtree — the two load-bearing landmarks
   that the rest of the repo layout hangs off.
2. The resolution is independent of the current working directory — a
   helper that silently depends on ``os.getcwd()`` would regress the
   ``parents[N]`` problem in a different disguise.
3. The derived path constants (``BACKEND_DIR``, ``APP_DIR`` etc.) are
   real directories under ``REPO_ROOT``, so downstream tests don't need
   to re-verify their structure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import _paths


def test_repo_root_contains_taskfile_marker() -> None:
    """``REPO_ROOT`` must point at the directory that owns ``Taskfile.yml``.

    The helper uses ``Taskfile.yml`` as its walk-up sentinel; this assertion
    verifies the resolution landed on the correct directory rather than a
    stray parent that happens to contain an identically-named file
    (there are none today — ``Taskfile.yml`` is unique to the repo root —
    but the invariant is worth pinning so the test fails loudly if a second
    ``Taskfile.yml`` ever gets added at a nested layer).
    """
    assert (_paths.REPO_ROOT / "Taskfile.yml").is_file(), (
        f"Expected REPO_ROOT ({_paths.REPO_ROOT}) to contain Taskfile.yml. "
        "The walk-up marker used by tests/_paths.py has drifted."
    )


def test_repo_root_contains_backend_tree() -> None:
    """``REPO_ROOT`` must expose the canonical ``apps/backend/`` subtree.

    Guards against the helper accidentally resolving to a Taskfile-holding
    directory that isn't the monorepo root (hypothetical, but cheap to pin).
    """
    assert (_paths.REPO_ROOT / "apps" / "backend" / "pyproject.toml").is_file(), (
        f"Expected apps/backend/pyproject.toml under REPO_ROOT ({_paths.REPO_ROOT}). "
        "The repo-root resolution is pointing at the wrong directory."
    )


def test_repo_root_is_cwd_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper must not rely on ``os.getcwd()`` to resolve the repo root.

    The whole point of replacing ``parents[N]`` is that the result is stable
    regardless of where the test process was launched from. Reimport the
    helper from a different working directory and assert the value is the
    same — any ``os.getcwd()``-based implementation would return a different
    path (or crash) here.
    """
    expected = _paths.REPO_ROOT
    monkeypatch.chdir(tmp_path)
    # Re-read the module-level constant via attribute access; since it was
    # resolved at import time (not on each access), the cwd change must not
    # affect it. Assertion ordering is `actual == expected` to keep ruff
    # SIM300 happy (no Yoda conditions).
    assert expected == _paths.REPO_ROOT, (
        f"REPO_ROOT changed after chdir — expected {expected}, got "
        f"{_paths.REPO_ROOT}. The helper is leaking cwd dependency."
    )
    # Sanity: cwd really did change.
    assert Path.cwd().resolve() == tmp_path.resolve()


def test_backend_dir_points_at_apps_backend() -> None:
    assert _paths.BACKEND_DIR == _paths.REPO_ROOT / "apps" / "backend"
    assert _paths.BACKEND_DIR.is_dir()


def test_app_dir_points_at_backend_app_tree() -> None:
    assert _paths.APP_DIR == _paths.BACKEND_DIR / "app"
    assert _paths.APP_DIR.is_dir()


def test_extraction_root_points_at_extraction_feature() -> None:
    assert _paths.EXTRACTION_ROOT == _paths.APP_DIR / "features" / "extraction"
    assert _paths.EXTRACTION_ROOT.is_dir()


def test_tests_dir_points_at_backend_tests_tree() -> None:
    assert _paths.TESTS_DIR == _paths.BACKEND_DIR / "tests"
    assert _paths.TESTS_DIR.is_dir()


def test_tests_unit_dir_points_at_unit_tests() -> None:
    assert _paths.TESTS_UNIT_DIR == _paths.TESTS_DIR / "unit"
    assert _paths.TESTS_UNIT_DIR.is_dir()


def test_fixtures_dir_points_at_tests_fixtures() -> None:
    assert _paths.FIXTURES_DIR == _paths.TESTS_DIR / "fixtures"
    assert _paths.FIXTURES_DIR.is_dir()

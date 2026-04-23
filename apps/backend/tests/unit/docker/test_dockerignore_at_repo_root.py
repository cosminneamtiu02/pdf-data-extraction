"""Static assertions on the repo-root `.dockerignore` (issue #269).

Docker only honors a `.dockerignore` that sits adjacent to the build-context
root. The canonical build command for this repo — pinned in both
`.github/workflows/deploy.yml` and `task docker:build` — is:

    docker build -f infra/docker/backend.Dockerfile -t <tag> .

i.e. the context root is the *repo root*, not `apps/backend/`. A
`.dockerignore` placed at `apps/backend/.dockerignore` has zero effect on
this build: the daemon receives the whole monorepo (`.claude/worktrees/`,
`.venv/`, `.hypothesis/`, `.import_linter_cache/`, full git history, docs,
every test tree) as the context tarball, slowing builds and invalidating
the cache on unrelated file changes.

This test pins the fix so a future PR cannot silently regress it by
re-introducing the misplaced per-slice file or deleting the root one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from tests._paths import REPO_ROOT

_ROOT_DOCKERIGNORE: Final[Path] = REPO_ROOT / ".dockerignore"
_MISPLACED_DOCKERIGNORE: Final[Path] = REPO_ROOT / "apps" / "backend" / ".dockerignore"

# Exclusions that MUST appear in the root .dockerignore. These are the
# directories/globs whose inclusion in the build context either bloats the
# daemon upload, poisons the build cache, or leaks local-only state into
# the image. Each entry is expressed exactly as it must appear in the file.
# The large non-runtime trees (`apps/backend/tests/`, `apps/backend/architecture/`,
# `apps/backend/fixtures/`, `.github/`) are not COPYd by
# `infra/docker/backend.Dockerfile` but would otherwise be tarballed into the
# build context, which is the regression issue #269 set out to fix.
_REQUIRED_EXCLUSIONS: Final[tuple[str, ...]] = (
    ".git/",
    ".claude/",
    "**/.venv/",
    "**/__pycache__/",
    "**/.pytest_cache/",
    "**/.ruff_cache/",
    "**/.import_linter_cache/",
    "docs/",
    "apps/backend/tests/",
    "apps/backend/architecture/",
    "apps/backend/fixtures/",
    ".github/",
)


def _read_root_dockerignore_lines() -> list[str]:
    """Return the non-comment, non-blank lines of the root `.dockerignore`.

    Fails the calling test with a clear message if the file is missing,
    instead of letting `read_text()` raise a bare `FileNotFoundError`. This
    keeps the error surface consistent with `test_root_dockerignore_exists`
    even if a future test-collection order runs the exclusion assertion
    first, or if someone deletes `.dockerignore` while iterating.
    """
    if not _ROOT_DOCKERIGNORE.is_file():
        pytest.fail(
            f"expected repo-root .dockerignore at {_ROOT_DOCKERIGNORE} — see "
            "`test_root_dockerignore_exists` for the root-cause diagnostic. "
            "Issue #269."
        )
    text = _ROOT_DOCKERIGNORE.read_text(encoding="utf-8")
    return [
        stripped
        for raw_line in text.splitlines()
        if (stripped := raw_line.strip()) and not stripped.startswith("#")
    ]


def test_read_helper_fails_cleanly_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_read_root_dockerignore_lines` must raise `Failed`, not `FileNotFoundError`.

    Point `_ROOT_DOCKERIGNORE` at a non-existent path under `tmp_path` and
    assert the helper surfaces a `pytest.fail(...)` with a clear diagnostic
    instead of propagating a raw `FileNotFoundError` from `read_text()`.
    """
    missing = tmp_path / ".dockerignore-does-not-exist"
    monkeypatch.setattr(
        "tests.unit.docker.test_dockerignore_at_repo_root._ROOT_DOCKERIGNORE",
        missing,
    )
    with pytest.raises(pytest.fail.Exception) as excinfo:
        _read_root_dockerignore_lines()
    assert "expected repo-root .dockerignore" in str(excinfo.value)


def test_root_dockerignore_exists() -> None:
    """A `.dockerignore` file must exist at the repo root.

    Without it, `docker build -f infra/docker/backend.Dockerfile .` sends
    the entire repo (including local-only caches and the `.claude/` worktree
    tree) to the daemon as context.
    """
    assert _ROOT_DOCKERIGNORE.is_file(), (
        f"expected repo-root .dockerignore at {_ROOT_DOCKERIGNORE} — Docker only "
        "honors .dockerignore adjacent to the build context, and the build "
        "context is `.` (repo root) per .github/workflows/deploy.yml and "
        "`task docker:build`. See issue #269."
    )


def test_misplaced_dockerignore_removed() -> None:
    """The old `apps/backend/.dockerignore` must NOT exist.

    Keeping a file with that name around is a footgun: it looks effective,
    but Docker ignores it entirely when the build context is the repo root.
    The fix is to delete it and centralize all exclusions in the root file.
    """
    assert not _MISPLACED_DOCKERIGNORE.exists(), (
        f"{_MISPLACED_DOCKERIGNORE} is misplaced — Docker ignores .dockerignore "
        "files that are not adjacent to the build-context root. Delete it and "
        "rely on the repo-root .dockerignore. See issue #269."
    )


def test_root_dockerignore_contains_critical_exclusions() -> None:
    """The root `.dockerignore` must list every critical exclusion.

    Missing any one of these would leak the corresponding tree into the
    build context: `.git/` is the largest per-repo offender; `.claude/`
    carries parallel-agent worktrees (issue #267); `**/.venv/` carries the
    full Python virtualenv; cache directories (`__pycache__`, `.pytest_cache`,
    `.ruff_cache`, `.import_linter_cache`) invalidate Docker layer cache on
    unrelated file changes. `docs/` is not needed at runtime.
    """
    lines = _read_root_dockerignore_lines()
    missing = [entry for entry in _REQUIRED_EXCLUSIONS if entry not in lines]
    assert not missing, (
        f"root .dockerignore at {_ROOT_DOCKERIGNORE} is missing required "
        f"exclusions: {missing!r}. Existing entries: {lines!r}. See issue #269."
    )

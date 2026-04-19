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

# parents[5] walks: this file -> docker/ -> unit/ -> tests/ -> backend/ ->
# apps/ -> repo root. This mirrors the convention used by
# `tests/unit/architecture/_linter_subprocess.REPO_ROOT`.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_ROOT_DOCKERIGNORE: Final[Path] = _REPO_ROOT / ".dockerignore"
_MISPLACED_DOCKERIGNORE: Final[Path] = _REPO_ROOT / "apps" / "backend" / ".dockerignore"

# Exclusions that MUST appear in the root .dockerignore. These are the
# directories/globs whose inclusion in the build context either bloats the
# daemon upload, poisons the build cache, or leaks local-only state into
# the image. Each entry is expressed exactly as it must appear in the file.
_REQUIRED_EXCLUSIONS: Final[tuple[str, ...]] = (
    ".git/",
    ".claude/",
    "**/.venv/",
    "**/__pycache__/",
    "**/.pytest_cache/",
    "**/.ruff_cache/",
    "**/.import_linter_cache/",
    "docs/",
)


def _read_root_dockerignore_lines() -> list[str]:
    """Return the non-comment, non-blank lines of the root `.dockerignore`."""
    text = _ROOT_DOCKERIGNORE.read_text(encoding="utf-8")
    return [
        stripped
        for raw_line in text.splitlines()
        if (stripped := raw_line.strip()) and not stripped.startswith("#")
    ]


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

"""Central repo-root and repo-relative path constants for the test suite.

Issue #402: eight+ test files hardcoded ``Path(__file__).resolve().parents[N]``
with N varying (3, 4, 5) by file depth to reach the repo root. Any future
directory reshuffle silently broke every hardcoded literal. This module
centralises the resolution so moves to the tree layout touch one line.

How it works:

- ``REPO_ROOT`` is resolved at import time by walking up from this file
  looking for the stable marker file ``Taskfile.yml`` (which is unique to
  the repo root — the only ``Taskfile.yml`` in the repo). The walk is
  bounded (``_MAX_WALK_DEPTH``) so a misconfigured checkout fails loudly
  instead of climbing forever toward ``/``.
- The walk runs *once* per interpreter, at module import, so the result
  is cached in the ``Final`` module-level constants. Subsequent accesses
  are trivial attribute reads.
- Resolution is independent of ``os.getcwd()`` — ``Path(__file__)`` is an
  absolute location pinned by the importer, so switching working
  directories (as tests often do via ``monkeypatch.chdir`` or
  ``tmp_path``) cannot affect the constants.

Why a module under ``tests/`` and not ``conftest.py``:

- ``conftest.py`` fixtures are lazily evaluated per test; the callers
  here want module-level ``Final[Path]`` constants that can be used in
  top-of-file assignments (e.g. ``_REPO_ROOT = ...`` before any test
  function runs). A plain importable module fits that shape cleanly.
- The leading underscore in the file name (``_paths.py``) mirrors the
  existing ``_linter_subprocess.py`` convention in
  ``tests/unit/architecture/``: internal test-support code, not a test
  module itself.

Callers that previously wrote ``Path(__file__).resolve().parents[5]``
or ``Path(__file__).resolve().parents[3]`` should import the relevant
constant(s) from this module instead. If a brand-new path landmark is
needed, add it here (keyed off ``REPO_ROOT`` or an existing derivative)
rather than reintroducing the ``parents[N]`` pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# Upper bound for the walk-up search. The repo's deepest test file sits
# about six levels below ``Taskfile.yml`` (e.g.
# ``apps/backend/tests/unit/features/extraction/parsing/test_X.py``), so
# walking up 15 levels gives comfortable headroom for future nesting while
# still failing loudly if this module is ever relocated outside the repo.
_MAX_WALK_DEPTH: Final[int] = 15

# Marker file used to identify the repo root during the walk-up. Verified
# unique to the repo root on 2026-04-23: ``find . -name Taskfile.yml``
# returned exactly one hit. Chosen over ``.git`` because in git worktrees
# that's a file rather than a directory and has been a historical footgun
# for repo-detection helpers elsewhere.
_REPO_ROOT_MARKER: Final[str] = "Taskfile.yml"


def _resolve_repo_root() -> Path:
    """Walk up from this file until we find ``Taskfile.yml``; return that dir.

    Bounded by ``_MAX_WALK_DEPTH`` so a bad checkout or an accidental move
    of this module surfaces as a ``RuntimeError`` at import time rather
    than an infinite loop.
    """
    current = Path(__file__).resolve()
    for _ in range(_MAX_WALK_DEPTH):
        current = current.parent
        if (current / _REPO_ROOT_MARKER).is_file():
            return current
    msg = (
        f"tests/_paths.py could not find {_REPO_ROOT_MARKER!r} by walking up "
        f"at most {_MAX_WALK_DEPTH} parents from {Path(__file__).resolve()}. "
        "The module may have been moved outside the repo, or the marker file "
        "has been renamed. Update _REPO_ROOT_MARKER or restore the file."
    )
    raise RuntimeError(msg)


REPO_ROOT: Final[Path] = _resolve_repo_root()
"""Absolute path to the monorepo root (the directory that owns ``Taskfile.yml``)."""

BACKEND_DIR: Final[Path] = REPO_ROOT / "apps" / "backend"
"""Absolute path to ``apps/backend/`` (the Python project root used by pytest)."""

APP_DIR: Final[Path] = BACKEND_DIR / "app"
"""Absolute path to ``apps/backend/app/`` — the FastAPI application package."""

EXTRACTION_ROOT: Final[Path] = APP_DIR / "features" / "extraction"
"""Absolute path to the extraction vertical slice under ``app/features/``."""

TESTS_DIR: Final[Path] = BACKEND_DIR / "tests"
"""Absolute path to ``apps/backend/tests/`` (the root of the test tree)."""

TESTS_UNIT_DIR: Final[Path] = TESTS_DIR / "unit"
"""Absolute path to ``apps/backend/tests/unit/`` (the unit test package)."""

FIXTURES_DIR: Final[Path] = TESTS_DIR / "fixtures"
"""Absolute path to ``apps/backend/tests/fixtures/`` (shared PDF fixtures etc.)."""

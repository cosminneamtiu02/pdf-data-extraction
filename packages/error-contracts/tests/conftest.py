"""Shared pytest fixtures for the error-contracts test suite.

Both ``test_generate_cli.py`` and ``test_generate_all_script.py`` need to
stage the ``scripts/`` package into a ``tmp_path`` so subprocess-level
``python -m scripts.<name>`` invocations resolve imports without touching
the live ``apps/backend/app/exceptions/_generated`` tree. Previously each
test file inlined its own copy of the staging helper; a reviewer flagged
that duplication (PR #499) as drift-prone — if ``scripts/`` ever grows a
non-``.py`` support file, only one copy would know to stage it. This
shared fixture is the one place that decides what "staged scripts"
means.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _stage_scripts_into(work: Path) -> None:
    """Copy every ``.py`` file under ``scripts/`` into ``work/scripts/``.

    Non-``.py`` files are deliberately skipped — the codegen package is
    pure-Python and any support file (YAML, JSON, config) lives under the
    package root, not inside ``scripts/``. If that ever changes, update
    this helper rather than the individual tests.
    """
    package_root = Path(__file__).resolve().parents[1]
    scripts_src = package_root / "scripts"
    scripts_dst = work / "scripts"
    scripts_dst.mkdir()
    for src in scripts_src.iterdir():
        if src.is_file() and src.suffix == ".py":
            (scripts_dst / src.name).write_text(src.read_text())


@pytest.fixture
def staged_scripts_dir(tmp_path: Path) -> Path:
    """Return a temp directory with ``scripts/`` staged alongside for -m invocations.

    The returned path is the working directory to pass as ``cwd=`` to
    ``subprocess.run([sys.executable, "-m", "scripts.<name>", ...])``;
    Python's module resolution will pick up the staged ``scripts/``
    package from the cwd automatically, so tests don't need to mutate
    ``PYTHONPATH``.
    """
    work = tmp_path / "work"
    work.mkdir()
    _stage_scripts_into(work)
    return work

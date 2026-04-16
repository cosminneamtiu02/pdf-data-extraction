"""Internal helper for the architecture test suite (PDFX-E007-F004).

Centralizes subprocess invocation of `lint-imports`, repo-root path
computation, and shared `_inject` helper used by the meta-enforcement
and live-subprocess tests. Kept out of `conftest.py` so the names are
explicitly imported (and grep-able) rather than fixture-injected.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

# parents[5] resolves the repo root by walking five levels up from this file:
# _linter_subprocess.py -> architecture/ -> unit/ -> tests/ -> backend/ -> apps/ -> repo
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
BACKEND_DIR: Final[Path] = REPO_ROOT / "apps" / "backend"
REAL_APP_TREE: Final[Path] = BACKEND_DIR / "app"
REAL_CONTRACTS_PATH: Final[Path] = BACKEND_DIR / "architecture" / "import-linter-contracts.ini"


def resolve_lint_imports_binary() -> Path:
    """Return the path to the `lint-imports` console script in the active venv.

    `python -m importlinter` does not work because the package has no
    `__main__.py`. The CLI is only installed as a console script entry point
    in the venv's `bin/` directory, so the portable invocation is to find
    that script next to `sys.executable` and exec it directly.
    """
    bin_name = "lint-imports.exe" if os.name == "nt" else "lint-imports"
    candidate = Path(sys.executable).parent / bin_name
    if not candidate.exists():
        msg = (
            f"could not locate lint-imports binary next to sys.executable "
            f"({sys.executable}); expected {candidate}"
        )
        raise FileNotFoundError(msg)
    return candidate


def run_lint_imports(
    cwd: Path,
    contracts_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Invoke `lint-imports --config <contracts_path>` from `cwd`.

    `cwd` is the directory that holds the `app/` tree the linter should
    analyze. The function ensures `cwd` is on `PYTHONPATH` so that
    `root_package = app` from the contracts file resolves against the tree
    in `cwd`, not against any other `app` package that might already be
    importable.

    `os.environ.copy()` is used to inherit the parent process environment.
    The CLAUDE.md prohibition on `os.environ` targets reading config values
    in production code; building a subprocess environment dict here has no
    pydantic-settings equivalent and is the documented way to pass env vars
    to child processes.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{cwd}{os.pathsep}{env.get('PYTHONPATH', '')}"
    return subprocess.run(
        [str(resolve_lint_imports_binary()), "--config", str(contracts_path)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def copy_app_tree(tmp_path: Path) -> Path:
    """Copy the real `apps/backend/app/` tree into `tmp_path/app` and return its root."""
    import shutil

    dest = tmp_path / "app"
    shutil.copytree(
        REAL_APP_TREE,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return dest


def copy_contracts(tmp_path: Path) -> Path:
    """Copy the real contracts INI into `tmp_path` and return the destination path."""
    dest = tmp_path / "import-linter-contracts.ini"
    dest.write_bytes(REAL_CONTRACTS_PATH.read_bytes())
    return dest


def inject_import_line(target: Path, line: str) -> None:
    """Prepend `line` to `target` while preserving any leading `from __future__` imports.

    Python requires `from __future__ import ...` statements to be the very
    first statement in a module. Naive prepending would put the injected
    line above a `__future__` import and trigger a SyntaxError, which the
    static AST parser used by import-linter (via `grimp`) would either skip
    or error on - either way invalidating the meta-enforcement test.

    This function:
      1. Walks the file's leading lines, preserving any `from __future__`
         imports as a head block.
      2. Inserts the injected line directly after the head block.
      3. Writes the rebuilt file back atomically (single `write_text` call).
    """
    if not target.exists():
        msg = f"scratch-tree target does not exist: {target}"
        raise FileNotFoundError(msg)

    original_lines = target.read_text().splitlines(keepends=True)
    head: list[str] = []
    body_start = 0
    for idx, raw_line in enumerate(original_lines):
        stripped = raw_line.strip()
        if stripped.startswith("from __future__"):
            head.append(raw_line)
            body_start = idx + 1
        elif stripped == "" and head:
            # Trailing blank line after a future import block - keep it with the head.
            head.append(raw_line)
            body_start = idx + 1
        else:
            break

    rebuilt = "".join(head) + line + "\n" + "".join(original_lines[body_start:])
    target.write_text(rebuilt)

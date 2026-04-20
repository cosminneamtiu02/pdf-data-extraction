"""Read-only drift check for errors.yaml against generated files.

Generates Python/TypeScript/JSON outputs into a temporary directory,
then compares them byte-for-byte against the live destination paths,
exiting non-zero with a diff summary on drift. The working tree is
never modified.

Replaces the prior `errors:check` pattern that ran `errors:generate`
first (mutating the working tree) and then called `git diff`. Leaving
the working tree modified surprised local devs who expected a read-only
verification step. See issue #291.
"""

from __future__ import annotations

import filecmp
import sys
import tempfile
from pathlib import Path

from scripts.generate import (
    generate_python,
    generate_required_keys,
    generate_typescript,
)

_ERROR_CONTRACTS_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _ERROR_CONTRACTS_DIR.parents[1]
_ERRORS_YAML = _ERROR_CONTRACTS_DIR / "errors.yaml"
_LIVE_PYTHON_DIR = _REPO_ROOT / "apps" / "backend" / "app" / "exceptions" / "_generated"
_LIVE_TS = _ERROR_CONTRACTS_DIR / "src" / "generated.ts"
_LIVE_REQUIRED_KEYS = _ERROR_CONTRACTS_DIR / "src" / "required-keys.json"


def _is_source_file(path: Path) -> bool:
    """Return True for files we care about; skip bytecode cache dirs."""
    return path.is_file() and "__pycache__" not in path.parts


def _collect_dir_drift(expected: Path, actual: Path) -> list[str]:
    expected_files: set[Path] = {
        p.relative_to(expected) for p in expected.rglob("*") if _is_source_file(p)
    }
    actual_files: set[Path] = set()
    if actual.exists():
        actual_files = {
            p.relative_to(actual) for p in actual.rglob("*") if _is_source_file(p)
        }
    drift: list[str] = []
    for rel in sorted(expected_files - actual_files):
        drift.append(f"missing in live tree: {actual / rel}")
    for rel in sorted(actual_files - expected_files):
        drift.append(f"extra in live tree: {actual / rel}")
    for rel in sorted(expected_files & actual_files):
        if not filecmp.cmp(expected / rel, actual / rel, shallow=False):
            drift.append(f"content drift: {actual / rel}")
    return drift


def _file_drift(expected: Path, actual: Path, label: str) -> list[str]:
    if not actual.exists():
        return [f"missing in live tree: {actual} ({label})"]
    if not filecmp.cmp(expected, actual, shallow=False):
        return [f"content drift: {actual} ({label})"]
    return []


def run_check(
    errors_yaml: Path = _ERRORS_YAML,
    live_python_dir: Path = _LIVE_PYTHON_DIR,
    live_ts: Path = _LIVE_TS,
    live_required_keys: Path = _LIVE_REQUIRED_KEYS,
) -> list[str]:
    """Generate into a temp dir and return the drift list against live paths.

    Pure function over the supplied paths — takes no module-level state and
    performs no I/O outside the temp dir and the provided live paths. This
    shape lets unit tests point `live_*` at `tmp_path` fixtures and drive
    pass/fail / missing / extra / content-drift cases without touching the
    real monorepo layout. (PR #312 review follow-up.)
    """
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        tmp_python = tmp / "python"
        tmp_python.mkdir()
        tmp_ts = tmp / "generated.ts"
        tmp_keys = tmp / "required-keys.json"

        generate_python(errors_yaml, tmp_python)
        generate_typescript(errors_yaml, tmp_ts)
        generate_required_keys(errors_yaml, tmp_keys)

        drift: list[str] = []
        drift.extend(_collect_dir_drift(tmp_python, live_python_dir))
        drift.extend(_file_drift(tmp_ts, live_ts, "typescript"))
        drift.extend(_file_drift(tmp_keys, live_required_keys, "required-keys"))
    return drift


def main() -> int:
    drift = run_check()
    if drift:
        sys.stderr.write("Error contract drift detected:\n")
        for item in drift:
            sys.stderr.write(f"  - {item}\n")
        sys.stderr.write("\nRun `task errors:generate` and commit the result.\n")
        return 1
    sys.stdout.write("Error contract files are in sync with errors.yaml.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

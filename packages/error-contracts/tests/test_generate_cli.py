"""Tests for the ``scripts.generate`` CLI entrypoint (issue #372).

Before this change the codegen functions in ``scripts/generate.py`` were
only callable programmatically; running the module standalone fell
through to nothing. The Taskfile + CI used ``python -m scripts.generate_all``
(extracted for issue #365) instead of the fragile inline
``python -c 'from scripts.generate import …'`` string. But the unwrapped
module still lacked a ``__main__`` block, so a developer trying
``python -m scripts.generate`` from a shell got no effect — the exact
footgun issue #372 names.

These tests pin the new entrypoint contract:

1. ``python -m scripts.generate --help`` exits 0 and prints usage text,
   proving an ``argparse`` CLI is wired up.
2. ``python -m scripts.generate --errors-yaml X --python-dir Y
   --typescript-path Z --required-keys-path W`` produces all three
   artifact families against supplied paths without mutating the live
   ``apps/backend/app/exceptions/_generated`` tree — the same isolation
   pattern ``test_generate_all_script.py`` uses.

The individual generator functions have their own coverage in
``test_generate.py``; this file is scoped to the CLI surface only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SAMPLE_YAML = """
version: 1
errors:
  NOT_FOUND:
    http_status: 404
    description: Resource not found
    params: {}
  WIDGET_NOT_FOUND:
    http_status: 404
    description: Widget not found
    params:
      widget_id: string
"""


def _stage_scripts(tmp_path: Path) -> Path:
    """Copy the real ``scripts/`` package into ``tmp_path`` and return its parent.

    ``python -m scripts.generate`` resolves ``scripts`` from ``sys.path``,
    which Python populates with the current working directory. Laying the
    script package alongside a per-test ``errors.yaml`` means we exercise
    the exact command shape the Taskfile and CI run without mutating the
    real monorepo layout (mirrors the fixture setup in
    ``test_generate_all_script.py``).
    """
    package_root = Path(__file__).resolve().parents[1]
    scripts_src = package_root / "scripts"
    work = tmp_path / "work"
    work.mkdir()
    scripts_dst = work / "scripts"
    scripts_dst.mkdir()
    for src in scripts_src.iterdir():
        if src.is_file() and src.suffix == ".py":
            (scripts_dst / src.name).write_text(src.read_text())
    return work


def test_generate_help_exits_zero_and_shows_usage(tmp_path: Path) -> None:
    """``python -m scripts.generate --help`` must return 0 with usage text.

    This is the load-bearing signal that ``generate.py`` has a proper
    ``argparse``-based ``__main__`` entrypoint — the whole point of
    issue #372. If this ever regresses (e.g. someone deletes the
    ``__main__`` block), the test fails with a non-zero return code
    because ``python -m`` on a module with no ``__main__`` returns
    exit 0 but produces no usage output.
    """
    work = _stage_scripts(tmp_path)
    proc = subprocess.run(  # noqa: S603 — sys.executable is trusted
        [sys.executable, "-m", "scripts.generate", "--help"],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
    # argparse emits usage to stdout for --help.
    assert "usage:" in proc.stdout.lower()
    # Ensure the CLI advertises the four path overrides (defaults live on
    # the module constants; advertising them keeps the module self-documenting
    # without needing the Taskfile or CI as the sole source of invocation
    # shape).
    assert "--errors-yaml" in proc.stdout
    assert "--python-dir" in proc.stdout
    assert "--typescript-path" in proc.stdout
    assert "--required-keys-path" in proc.stdout


def test_generate_cli_writes_all_three_artifact_families(tmp_path: Path) -> None:
    """End-to-end: ``python -m scripts.generate <paths>`` writes Py + TS + JSON.

    Confirms the new CLI is observationally equivalent to calling the three
    generator functions directly — i.e. the ``__main__`` block doesn't drop
    a generator, shuffle arguments, or fail silently.
    """
    work = _stage_scripts(tmp_path)
    errors_yaml = work / "errors.yaml"
    errors_yaml.write_text(SAMPLE_YAML)

    python_dir = work / "python_out"
    ts_path = work / "generated.ts"
    keys_path = work / "required-keys.json"

    proc = subprocess.run(  # noqa: S603 — sys.executable is trusted
        [
            sys.executable,
            "-m",
            "scripts.generate",
            "--errors-yaml",
            str(errors_yaml),
            "--python-dir",
            str(python_dir),
            "--typescript-path",
            str(ts_path),
            "--required-keys-path",
            str(keys_path),
        ],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"

    # Python artifacts: __init__.py + _registry.py + one file per error class
    # (plus Params class files where params are declared).
    python_files = {p.name for p in python_dir.iterdir() if p.is_file()}
    assert "__init__.py" in python_files
    assert "_registry.py" in python_files
    assert "not_found_error.py" in python_files
    assert "widget_not_found_error.py" in python_files

    # TypeScript artifact.
    assert ts_path.exists()
    assert "export type ErrorCode" in ts_path.read_text()

    # required-keys.json artifact.
    assert keys_path.exists()
    payload = json.loads(keys_path.read_text())
    assert payload["namespace"] == "errors"
    assert set(payload["keys"]) == {"NOT_FOUND", "WIDGET_NOT_FOUND"}

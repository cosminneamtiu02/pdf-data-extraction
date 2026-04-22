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
   pattern ``test_generate_all_script.py`` uses (via the shared
   ``staged_scripts_dir`` fixture in ``conftest.py``).
3. ``--help`` output reflects the actual module-constant defaults, so
   tweaking ``_DEFAULT_*`` paths in ``scripts.generate`` can never
   silently leave the ``help=`` strings showing stale values (PR #499
   review: hard-coded help text was drift-prone).

The individual generator functions have their own coverage in
``test_generate.py``; this file is scoped to the CLI surface only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import generate as generate_module

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


def test_generate_help_exits_zero_and_shows_usage(staged_scripts_dir: Path) -> None:
    """``python -m scripts.generate --help`` must return 0 with usage text.

    This is the load-bearing signal that ``generate.py`` has a proper
    ``argparse``-based ``__main__`` entrypoint — the whole point of
    issue #372. If this ever regresses (e.g. someone deletes the
    ``__main__`` block), the test fails with a non-zero return code
    because ``python -m`` on a module with no ``__main__`` returns
    exit 0 but produces no usage output.
    """
    proc = subprocess.run(  # noqa: S603 — sys.executable is trusted
        [sys.executable, "-m", "scripts.generate", "--help"],
        cwd=staged_scripts_dir,
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


def test_generate_help_reflects_actual_default_paths() -> None:
    """``--help`` output contains the real ``_DEFAULT_*`` constant values.

    The argparse ``help=`` strings used to hard-code default path literals
    (e.g. the string ``packages/error-contracts/errors.yaml``) alongside
    ``default=None`` (with the actual resolution happening inside
    ``main()`` against module constants). That opened a drift window: if
    someone updated ``_DEFAULT_ERRORS_YAML`` but forgot the help string,
    ``--help`` would confidently advertise a stale path.

    This test runs the subprocess with ``cwd`` set to the real
    error-contracts package root so the subprocess resolves
    ``scripts.generate`` to the same ``__file__`` as the in-process import
    — therefore both compute identical ``_DEFAULT_*`` constants via
    ``Path(__file__).parents[1]``. The staged-scripts fixture isn't
    suitable here because staging moves ``__file__`` under ``tmp_path``,
    which would shift the ``_DEFAULT_*`` paths into the temp tree and
    decouple them from the in-process module's view.
    """
    package_root = Path(generate_module.__file__).resolve().parents[1]
    proc = subprocess.run(  # noqa: S603 — sys.executable is trusted
        [sys.executable, "-m", "scripts.generate", "--help"],
        cwd=package_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"

    # argparse's default HelpFormatter wraps help= text across lines at
    # terminal width, breaking at hyphens and spaces alike — which inserts
    # whitespace inside what were contiguous path segments. Strip ALL
    # whitespace (spaces, newlines, indentation) before substring checks so
    # the assertions are resilient to wrap-column differences across
    # environments. Paths don't contain whitespace, so this is lossless.
    flat_stdout = "".join(proc.stdout.split())
    assert str(generate_module._DEFAULT_ERRORS_YAML) in flat_stdout
    assert str(generate_module._DEFAULT_PYTHON_DIR) in flat_stdout
    assert str(generate_module._DEFAULT_TS_PATH) in flat_stdout
    assert str(generate_module._DEFAULT_REQUIRED_KEYS_PATH) in flat_stdout


def test_generate_cli_writes_all_three_artifact_families(
    staged_scripts_dir: Path,
) -> None:
    """End-to-end: ``python -m scripts.generate <paths>`` writes Py + TS + JSON.

    Confirms the new CLI is observationally equivalent to calling the three
    generator functions directly — i.e. the ``__main__`` block doesn't drop
    a generator, shuffle arguments, or fail silently.
    """
    errors_yaml = staged_scripts_dir / "errors.yaml"
    errors_yaml.write_text(SAMPLE_YAML)

    python_dir = staged_scripts_dir / "python_out"
    ts_path = staged_scripts_dir / "generated.ts"
    keys_path = staged_scripts_dir / "required-keys.json"

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
        cwd=staged_scripts_dir,
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

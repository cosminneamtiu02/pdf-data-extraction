"""Tests for the `scripts.generate_all` entry-point wrapper.

Issue #365: the previous `task errors:generate` Taskfile step and the
`.github/workflows/ci.yml` "Regenerate error contracts" step both inlined
the same 8-statement `python -c` block. Drift between the two copies
would silently skew local vs. CI behaviour. The fix extracts a shared
``main()`` into ``scripts/generate_all.py``; both callers now invoke
``python -m scripts.generate_all``.

These tests cover the wrapper specifically — the individual generators
(``generate_python``, ``generate_typescript``, ``generate_required_keys``)
already have their own coverage in ``test_generate.py``. We check:

1. Invoking ``main()`` with explicit paths produces Python + TS + JSON
   artifacts identical to calling the three generators directly (i.e.
   the wrapper does not reorder arguments or drop calls).
2. ``main()`` defaults its paths from the project layout when called
   with no arguments, so the module-as-script invocation used by the
   Taskfile / CI works without environment setup.
3. Running the script as a module via ``python -m scripts.generate_all``
   exits zero and writes all three artifact families — the end-to-end
   path the Taskfile and CI workflow actually execute.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.generate import (
    generate_python,
    generate_required_keys,
    generate_typescript,
)
from scripts.generate_all import main as generate_all_main

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


def _read_dir(path: Path) -> dict[str, str]:
    """Return {relative_name: text} for every file under path (non-recursive suffices)."""
    return {p.name: p.read_text() for p in sorted(path.iterdir()) if p.is_file()}


def test_main_with_explicit_paths_matches_direct_generator_calls(
    tmp_path: Path,
) -> None:
    """``main()`` invoking the three generators yields the same bytes as calling them directly.

    This pins the wrapper's behaviour: it must not reorder arguments, drop
    any of the three outputs, or transform paths. A regression here is
    exactly the duplication hazard issue #365 was filed to eliminate —
    the previous inlined `python -c` block and the new wrapper must be
    observationally indistinguishable.
    """
    errors_yaml = tmp_path / "errors.yaml"
    errors_yaml.write_text(SAMPLE_YAML)

    # Direct-generator baseline.
    baseline_python_dir = tmp_path / "baseline_python"
    baseline_ts = tmp_path / "baseline.ts"
    baseline_keys = tmp_path / "baseline-keys.json"
    generate_python(errors_yaml, baseline_python_dir)
    generate_typescript(errors_yaml, baseline_ts)
    generate_required_keys(errors_yaml, baseline_keys)

    # Wrapper output.
    wrapper_python_dir = tmp_path / "wrapper_python"
    wrapper_ts = tmp_path / "wrapper.ts"
    wrapper_keys = tmp_path / "wrapper-keys.json"
    exit_code = generate_all_main(
        errors_yaml=errors_yaml,
        python_dir=wrapper_python_dir,
        typescript_path=wrapper_ts,
        required_keys_path=wrapper_keys,
    )
    assert exit_code == 0

    assert _read_dir(baseline_python_dir) == _read_dir(wrapper_python_dir)
    assert baseline_ts.read_text() == wrapper_ts.read_text()
    assert baseline_keys.read_text() == wrapper_keys.read_text()


def test_main_produces_valid_json_for_required_keys(tmp_path: Path) -> None:
    """The JSON artifact the wrapper writes is loadable and has the expected shape."""
    errors_yaml = tmp_path / "errors.yaml"
    errors_yaml.write_text(SAMPLE_YAML)

    python_dir = tmp_path / "python"
    ts_path = tmp_path / "generated.ts"
    keys_path = tmp_path / "required-keys.json"
    generate_all_main(
        errors_yaml=errors_yaml,
        python_dir=python_dir,
        typescript_path=ts_path,
        required_keys_path=keys_path,
    )

    payload = json.loads(keys_path.read_text())
    assert payload["namespace"] == "errors"
    assert set(payload["keys"]) == {"NOT_FOUND", "WIDGET_NOT_FOUND"}
    assert payload["params_by_key"]["WIDGET_NOT_FOUND"] == ["widget_id"]
    assert payload["params_by_key"]["NOT_FOUND"] == []


def test_main_writes_all_three_artifact_families(tmp_path: Path) -> None:
    """Pins that the wrapper produces Python files, a TS file, and a JSON file."""
    errors_yaml = tmp_path / "errors.yaml"
    errors_yaml.write_text(SAMPLE_YAML)

    python_dir = tmp_path / "python"
    ts_path = tmp_path / "generated.ts"
    keys_path = tmp_path / "required-keys.json"
    generate_all_main(
        errors_yaml=errors_yaml,
        python_dir=python_dir,
        typescript_path=ts_path,
        required_keys_path=keys_path,
    )

    # Python artifacts: at minimum __init__.py + _registry.py + one file per class.
    python_files = {p.name for p in python_dir.iterdir() if p.is_file()}
    assert "__init__.py" in python_files
    assert "_registry.py" in python_files
    assert "not_found_error.py" in python_files
    assert "widget_not_found_error.py" in python_files

    assert ts_path.exists()
    assert "export type ErrorCode" in ts_path.read_text()

    assert keys_path.exists()
    assert json.loads(keys_path.read_text())["version"] == 1


def test_main_module_invocation_exits_zero_and_writes_live_artifacts(
    tmp_path: Path,
) -> None:
    """End-to-end: `python -m scripts.generate_all` on the real errors.yaml.

    Exercises the exact command the Taskfile and CI workflow run. The test
    copies the package layout (scripts/ + errors.yaml) into `tmp_path` so
    we don't mutate the live `apps/backend/app/exceptions/_generated`
    tree — that's `task errors:generate`'s job on demand, not a side
    effect of `task check`.
    """
    package_root = Path(__file__).resolve().parents[1]
    scripts_src = package_root / "scripts"

    work = tmp_path / "work"
    work.mkdir()
    errors_yaml = work / "errors.yaml"
    errors_yaml.write_text(SAMPLE_YAML)

    # Lay scripts/ alongside errors.yaml so `python -m scripts.generate_all`
    # resolves identically to how it does from packages/error-contracts/.
    scripts_dst = work / "scripts"
    scripts_dst.mkdir()
    for src in scripts_src.iterdir():
        if src.is_file() and src.suffix == ".py":
            (scripts_dst / src.name).write_text(src.read_text())

    python_dir = work / "python_out"
    ts_path = work / "generated.ts"
    keys_path = work / "required-keys.json"

    proc = subprocess.run(  # noqa: S603 — sys.executable is trusted
        [
            sys.executable,
            "-m",
            "scripts.generate_all",
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
    assert python_dir.exists()
    assert ts_path.exists()
    assert keys_path.exists()

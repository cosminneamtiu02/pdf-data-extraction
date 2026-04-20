"""Unit tests for the `scripts.check` drift-verifier.

Covers the helper predicates (`_is_source_file`, `_collect_dir_drift`,
`_file_drift`) and the end-to-end `run_check` entrypoint with paths
pointed at `tmp_path` fixtures. None of these tests touch the real
repo's `_generated/` tree, so they stay hermetic across parallel agents
and future monorepo layout changes (PR #312 review follow-up).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.check import (
    _collect_dir_drift,
    _file_drift,
    _is_source_file,
    run_check,
)


# --- _is_source_file ---------------------------------------------------


def test_is_source_file_accepts_plain_python_file(tmp_path: Path) -> None:
    f = tmp_path / "widget_error.py"
    f.write_text("pass\n")
    assert _is_source_file(f) is True


def test_is_source_file_rejects_pycache_entry(tmp_path: Path) -> None:
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    f = cache_dir / "widget_error.cpython-313.pyc"
    f.write_bytes(b"\x00")
    assert _is_source_file(f) is False


def test_is_source_file_rejects_directory(tmp_path: Path) -> None:
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    assert _is_source_file(subdir) is False


# --- _collect_dir_drift ------------------------------------------------


def test_collect_dir_drift_empty_when_dirs_match(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    for base in (expected, actual):
        base.mkdir()
        (base / "a.py").write_text("x = 1\n")
        (base / "b.py").write_text("y = 2\n")
    assert _collect_dir_drift(expected, actual) == []


def test_collect_dir_drift_detects_missing_actual_file(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    expected.mkdir()
    actual.mkdir()
    (expected / "only_in_expected.py").write_text("pass\n")
    drift = _collect_dir_drift(expected, actual)
    assert len(drift) == 1
    assert "missing in live tree" in drift[0]
    assert "only_in_expected.py" in drift[0]


def test_collect_dir_drift_detects_extra_actual_file(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    expected.mkdir()
    actual.mkdir()
    (actual / "stale.py").write_text("pass\n")
    drift = _collect_dir_drift(expected, actual)
    assert len(drift) == 1
    assert "extra in live tree" in drift[0]
    assert "stale.py" in drift[0]


def test_collect_dir_drift_detects_content_drift(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    expected.mkdir()
    actual.mkdir()
    (expected / "same_name.py").write_text("version = 1\n")
    (actual / "same_name.py").write_text("version = 2\n")
    drift = _collect_dir_drift(expected, actual)
    assert len(drift) == 1
    assert "content drift" in drift[0]
    assert "same_name.py" in drift[0]


def test_collect_dir_drift_handles_missing_actual_dir(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    expected.mkdir()
    (expected / "a.py").write_text("pass\n")
    missing_actual = tmp_path / "does_not_exist"
    drift = _collect_dir_drift(expected, missing_actual)
    assert len(drift) == 1
    assert "missing in live tree" in drift[0]


def test_collect_dir_drift_ignores_pycache(tmp_path: Path) -> None:
    """Stale `.pyc` files in the actual tree must not register as drift."""
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    for base in (expected, actual):
        base.mkdir()
        (base / "a.py").write_text("pass\n")
    # Add a __pycache__ entry on the actual side only — must be ignored.
    cache = actual / "__pycache__"
    cache.mkdir()
    (cache / "a.cpython-313.pyc").write_bytes(b"\x00")
    assert _collect_dir_drift(expected, actual) == []


# --- _file_drift -------------------------------------------------------


def test_file_drift_empty_when_files_match(tmp_path: Path) -> None:
    expected = tmp_path / "expected.ts"
    actual = tmp_path / "actual.ts"
    expected.write_text("export const X = 1;\n")
    actual.write_text("export const X = 1;\n")
    assert _file_drift(expected, actual, "typescript") == []


def test_file_drift_detects_missing_actual_file(tmp_path: Path) -> None:
    expected = tmp_path / "expected.ts"
    expected.write_text("export const X = 1;\n")
    missing = tmp_path / "never_created.ts"
    drift = _file_drift(expected, missing, "typescript")
    assert len(drift) == 1
    assert "missing in live tree" in drift[0]
    assert "(typescript)" in drift[0]


def test_file_drift_detects_content_difference(tmp_path: Path) -> None:
    expected = tmp_path / "expected.ts"
    actual = tmp_path / "actual.ts"
    expected.write_text("export const X = 1;\n")
    actual.write_text("export const X = 2;\n")
    drift = _file_drift(expected, actual, "typescript")
    assert len(drift) == 1
    assert "content drift" in drift[0]
    assert "(typescript)" in drift[0]


# --- run_check end-to-end ----------------------------------------------

_MINIMAL_ERRORS_YAML = """\
version: 1
errors:
  SAMPLE_ERROR:
    http_status: 418
    description: Sample error
    params: {}
"""


def _write_errors_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "errors.yaml"
    p.write_text(_MINIMAL_ERRORS_YAML)
    return p


def _generate_live_tree(
    errors_yaml: Path, live_python: Path, live_ts: Path, live_keys: Path
) -> None:
    """Populate live paths from errors.yaml using the real generators."""
    from scripts.generate import (
        generate_python,
        generate_required_keys,
        generate_typescript,
    )

    live_python.mkdir(parents=True, exist_ok=True)
    generate_python(errors_yaml, live_python)
    generate_typescript(errors_yaml, live_ts)
    generate_required_keys(errors_yaml, live_keys)


def test_run_check_returns_empty_drift_when_in_sync(tmp_path: Path) -> None:
    errors_yaml = _write_errors_yaml(tmp_path)
    live_python = tmp_path / "live_py"
    live_ts = tmp_path / "live.ts"
    live_keys = tmp_path / "live_keys.json"
    _generate_live_tree(errors_yaml, live_python, live_ts, live_keys)

    drift = run_check(errors_yaml, live_python, live_ts, live_keys)
    assert drift == []


def test_run_check_reports_drift_when_ts_file_modified(tmp_path: Path) -> None:
    errors_yaml = _write_errors_yaml(tmp_path)
    live_python = tmp_path / "live_py"
    live_ts = tmp_path / "live.ts"
    live_keys = tmp_path / "live_keys.json"
    _generate_live_tree(errors_yaml, live_python, live_ts, live_keys)

    live_ts.write_text("// hand-edited stale content\n")
    drift = run_check(errors_yaml, live_python, live_ts, live_keys)
    assert any("content drift" in d and "(typescript)" in d for d in drift)


def test_run_check_reports_drift_when_live_ts_missing(tmp_path: Path) -> None:
    errors_yaml = _write_errors_yaml(tmp_path)
    live_python = tmp_path / "live_py"
    live_ts = tmp_path / "live.ts"
    live_keys = tmp_path / "live_keys.json"
    _generate_live_tree(errors_yaml, live_python, live_ts, live_keys)

    live_ts.unlink()
    drift = run_check(errors_yaml, live_python, live_ts, live_keys)
    assert any("missing in live tree" in d and "(typescript)" in d for d in drift)


def test_run_check_reports_drift_when_live_python_has_extra_file(
    tmp_path: Path,
) -> None:
    errors_yaml = _write_errors_yaml(tmp_path)
    live_python = tmp_path / "live_py"
    live_ts = tmp_path / "live.ts"
    live_keys = tmp_path / "live_keys.json"
    _generate_live_tree(errors_yaml, live_python, live_ts, live_keys)

    (live_python / "orphan_from_deleted_code.py").write_text("pass\n")
    drift = run_check(errors_yaml, live_python, live_ts, live_keys)
    assert any(
        "extra in live tree" in d and "orphan_from_deleted_code.py" in d for d in drift
    )


def _mutate_ts_content(ts: Path, keys: Path) -> None:
    del keys  # unused; lambda-equivalent takes both for uniform signature
    ts.write_text("// stale\n")


def _mutate_keys_content(ts: Path, keys: Path) -> None:
    del ts  # unused; lambda-equivalent takes both for uniform signature
    keys.write_text("{}\n")


@pytest.mark.parametrize(
    "mutator",
    [
        pytest.param(_mutate_ts_content, id="ts_content_drift"),
        pytest.param(_mutate_keys_content, id="keys_content_drift"),
    ],
)
def test_run_check_flags_per_artifact_drift(
    tmp_path: Path, mutator: Callable[[Path, Path], None]
) -> None:
    errors_yaml = _write_errors_yaml(tmp_path)
    live_python = tmp_path / "live_py"
    live_ts = tmp_path / "live.ts"
    live_keys = tmp_path / "live_keys.json"
    _generate_live_tree(errors_yaml, live_python, live_ts, live_keys)

    mutator(live_ts, live_keys)
    drift = run_check(errors_yaml, live_python, live_ts, live_keys)
    assert drift, "expected drift to be detected"

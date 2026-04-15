"""Unit tests for `scripts.validate_skills` — dev-loop skill validator.

Covers the nine unit scenarios in PDFX-E002-F004: happy path, empty dir,
filename-version mismatch, syntactically malformed YAML, missing path,
`-m` module invocation, default-Settings fallback, import containment
(no FastAPI / no heavy extraction deps), and a loose 10-skill perf bound.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.features.extraction.skills import skill_loader as skill_loader_module
from scripts.validate_skills import main, validate


class _SilentLogger:
    """Drop-in spy for `SkillLoader._logger` that swallows every call.

    `SkillLoader` emits a `skill_manifest_empty` warning on empty corpora.
    `structlog`'s global config differs between unit-only runs and full-
    session runs where `configure_logging()` has already been called, so
    the warning can leak into `capsys`'s captured stdout. Replacing the
    module-level logger with this stub keeps the validator's own stdout
    line the only thing captured — matching the test-spec contract of
    exact output equality without papering over real regressions.
    """

    def warning(self, event: str, **kwargs: object) -> None:
        del event, kwargs


def _write_skill(
    base: Path,
    *,
    dir_name: str,
    file_name: str = "1.yaml",
    version: int = 1,
    body_override: dict[str, Any] | None = None,
) -> Path:
    body: dict[str, Any] = {
        "name": dir_name,
        "version": version,
        "prompt": "Extract fields.",
        "examples": [{"input": "X-1", "output": {"number": "X-1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
    }
    if body_override:
        body.update(body_override)
    target_dir = base / dir_name
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / file_name
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def test_validate_three_valid_skills_exits_zero_and_prints_count(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    _write_skill(tmp_path, dir_name="invoice", file_name="2.yaml", version=2)
    _write_skill(tmp_path, dir_name="research_paper", file_name="1.yaml", version=1)

    code = validate(tmp_path)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "\u2714 3 skills validated\n"
    assert captured.err == ""


def test_validate_empty_directory_exits_zero_with_zero_count(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(skill_loader_module, "_logger", _SilentLogger())

    code = validate(tmp_path)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "\u2714 0 skills validated\n"


def test_validate_filename_version_mismatch_exits_nonzero_with_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # filename is 2.yaml but body says version: 1 → SkillLoader aggregates as mismatch.
    _write_skill(tmp_path, dir_name="invoice", file_name="2.yaml", version=1)

    code = validate(tmp_path)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "does not match" in captured.err
    assert "2.yaml" in captured.err


def test_validate_malformed_yaml_names_offending_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_skill(tmp_path, dir_name="valid", file_name="1.yaml", version=1)
    broken_dir = tmp_path / "broken"
    broken_dir.mkdir()
    broken_path = broken_dir / "1.yaml"
    broken_path.write_text("not: [valid: yaml", encoding="utf-8")

    code = validate(tmp_path)

    captured = capsys.readouterr()
    assert code == 1
    assert "broken/1.yaml" in captured.err or str(broken_path) in captured.err


def test_validate_missing_directory_exits_nonzero_with_message(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "nope"

    code = validate(missing)

    captured = capsys.readouterr()
    assert code == 1
    assert "nope" in captured.err
    assert "does not exist or is not a directory" in captured.err


def test_module_invocation_against_empty_dir_keeps_stdout_clean(tmp_path: Path) -> None:
    """Regression: `SkillLoader` warns on empty corpora via structlog; the CLI
    must route that warning to stderr so stdout stays a single clean result
    line. Runs the real entry point in a subprocess — any regression in
    `_configure_cli_logging` surfaces here, not just in the `_SilentLogger`
    monkeypatched unit tests.
    """
    result = subprocess.run(
        [sys.executable, "-m", "scripts.validate_skills", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "\u2714 0 skills validated\n"
    # The warning is allowed to appear on stderr, or be silently dropped —
    # the invariant is that stdout is exactly the result line.


def test_module_invocation_matches_programmatic_validate(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    _write_skill(tmp_path, dir_name="invoice", file_name="2.yaml", version=2)
    _write_skill(tmp_path, dir_name="research_paper", file_name="1.yaml", version=1)

    result = subprocess.run(
        [sys.executable, "-m", "scripts.validate_skills", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "\u2714 3 skills validated\n"
    assert result.stderr == ""


def test_main_with_no_argument_falls_back_to_settings_skills_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Point `Settings().skills_dir` at an empty tmp_path via env var, then
    # drop any pre-existing positional args so `main()` must use the fallback.
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate_skills"])
    monkeypatch.setattr(skill_loader_module, "_logger", _SilentLogger())

    code = main()

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "\u2714 0 skills validated\n"


def test_import_containment_no_fastapi_or_heavy_deps() -> None:
    """Importing the script must not pull in FastAPI / Docling / etc.

    The forbidden prefixes are the extraction stack's heavy runtime deps
    plus anything in the `app.main` tree (which would imply a FastAPI boot).
    Runs the check in a subprocess so the main test process's warmer
    `sys.modules` cannot mask a leaked import.
    """
    probe = (
        "import sys, importlib;"
        "importlib.import_module('scripts.validate_skills');"
        "forbidden = ('app.main', 'fastapi', 'docling', 'langextract', 'fitz', 'httpx');"
        "leaked = [m for m in sys.modules if m.startswith(forbidden)];"
        "print('LEAKED:' + ','.join(sorted(leaked)) if leaked else 'OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK", result.stdout


def test_validate_ten_skill_corpus_under_two_seconds(tmp_path: Path) -> None:
    for i in range(1, 11):
        _write_skill(tmp_path, dir_name=f"skill_{i}", file_name="1.yaml", version=1)

    start = time.monotonic()
    code = validate(tmp_path)
    elapsed = time.monotonic() - start

    assert code == 0
    assert elapsed < 2.0, f"validation took {elapsed:.2f}s, expected <2s"

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

from scripts.validate_skills import main, validate


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
        "description": f"{dir_name} extractor.",
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
) -> None:
    # The `contextlib.redirect_stdout(sys.stderr)` wrapper in `validate()`
    # ensures the `skill_manifest_empty` structlog warning never reaches
    # stdout — no monkeypatching needed.
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
    `validate()`'s `redirect_stdout` wrapper surfaces here, not just in
    same-process unit tests — a fresh interpreter gives structlog a fresh
    chance to write to stdout.
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

    code = main()

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "\u2714 0 skills validated\n"


def test_main_rejects_extra_positional_arguments(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A typo like `validate_skills ./skills /typo` must not silently succeed.

    Without this guard, `main()` reads only `args[0]` and drops the rest,
    which hides operator mistakes (original external finding).
    """
    monkeypatch.setattr(sys, "argv", ["validate_skills", "./a", "./b"])

    code = main()

    captured = capsys.readouterr()
    # Spec mandates a single non-zero exit code; usage errors share `1` with
    # validation failures so callers don't need to decode multiple statuses.
    assert code == 1
    assert captured.out == ""
    assert "expected at most 1 positional argument" in captured.err


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


def test_validate_accepts_string_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Programmatic callers may reasonably pass a string path.

    `validate()` normalizes via `Path(skills_dir)` before handing off to
    `SkillLoader`, so a string input behaves identically to a `Path`.
    """
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)

    code = validate(str(tmp_path))

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "\u2714 1 skills validated\n"


def test_validate_ten_skill_corpus_does_not_explode(tmp_path: Path) -> None:
    """Gross-regression bound for the fast-path validator.

    The spec's 2 s cold-start budget is about the overall CLI run including
    interpreter startup — the validator itself completes in ~50 ms locally
    against 10 skills. The test uses a generous 10 s ceiling so it still
    catches accidental O(n²) blowups or a heavy-dep leak while not flaking
    on a slow CI runner. The real "no heavy imports" guarantee lives in
    `test_import_containment_no_fastapi_or_heavy_deps`.
    """
    for i in range(1, 11):
        _write_skill(tmp_path, dir_name=f"skill_{i}", file_name="1.yaml", version=1)

    start = time.monotonic()
    code = validate(tmp_path)
    elapsed = time.monotonic() - start

    assert code == 0
    assert elapsed < 10.0, f"validation took {elapsed:.2f}s, expected <10s"

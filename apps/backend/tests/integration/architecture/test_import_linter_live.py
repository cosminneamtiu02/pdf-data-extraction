"""Live subprocess runs that prove import-linter passes against the real codebase.

Scenarios I1 and I2 from PDFX-E007-F004's verifiable spec. Unlike the
scratch-tree meta-enforcement tests in
`tests/unit/architecture/test_contract_enforcement.py`, these tests run
`lint-imports` against the actual `apps/backend/app/` tree. They are the
only tests in the suite that exercise the post-implementation contracts
against the post-implementation code, so they are the load-bearing
acceptance-criterion check for AC1.

I2 additionally exercises the Taskfile wrapper (`task check:arch`) which
is the path the developer-loop and CI both take. Together they prove the
contract set is intact end-to-end through the runner that `task check`
uses.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.unit.architecture._linter_subprocess import (
    BACKEND_DIR,
    REPO_ROOT,
    resolve_lint_imports_binary,
)


def test_lint_imports_passes_against_real_codebase() -> None:
    """I1 (AC1): lint-imports against the post-implementation codebase exits 0.

    All 11 contracts (the legacy `shared-no-features` plus C1, C2a-e, C3-C6)
    must report KEPT. Any contract reported as broken - or any nonzero exit
    code - is a hard CI failure and means PDFX-E007-F004's central acceptance
    criterion regressed.
    """
    binary = resolve_lint_imports_binary()

    result = subprocess.run(
        [str(binary), "--config", "architecture/import-linter-contracts.ini"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "lint-imports failed against the real codebase. "
        f"exit={result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    expected_contract_keywords = (
        "Shared and core",
        "C1:",
        "C2a:",
        "C2b:",
        "C2c:",
        "C2d:",
        "C2e:",
        "C3:",
        "C4:",
        "C5:",
        "C6:",
    )
    for keyword in expected_contract_keywords:
        assert keyword in result.stdout, (
            f"lint-imports output is missing expected contract '{keyword}'\n"
            f"STDOUT:\n{result.stdout}"
        )

    assert "0 broken" in result.stdout, (
        f"lint-imports output should report '0 broken'\nSTDOUT:\n{result.stdout}"
    )


def test_task_check_arch_runs_lint_imports_through_runner() -> None:
    """I2 (AC2): the `task check:arch` wrapper invokes lint-imports successfully.

    Exercises the same path the developer dev-loop and CI use, proving the
    Taskfile -> lint-imports plumbing is intact. Skipped gracefully if `task`
    is not installed on the host (e.g. on a minimal CI runner that calls
    pytest directly without go-task), since the unit-level Taskfile parse
    test (U6) already verifies the wiring statically.
    """
    task_binary = shutil.which("task")
    if task_binary is None:
        pytest.skip("`task` binary not installed; U6 covers Taskfile wiring statically")

    # pyright's pytest stubs don't model `pytest.skip` as NoReturn, so an
    # explicit narrowing assert is needed even though the line above raises
    # Skipped on the None branch and execution never reaches this point.
    assert task_binary is not None
    result = subprocess.run(
        [task_binary, "check:arch"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"`task check:arch` failed.\n"
        f"exit={result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "lint-imports" in result.stdout or "lint-imports" in result.stderr, (
        f"expected `task check:arch` output to mention lint-imports\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "0 broken" in result.stdout or "0 broken" in result.stderr, (
        f"expected `task check:arch` to report '0 broken'\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

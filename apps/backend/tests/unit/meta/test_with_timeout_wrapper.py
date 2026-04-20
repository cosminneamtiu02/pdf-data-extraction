"""Unit tests for the `infra/taskfile/with_timeout.py` command wrapper.

Tests the wrapper as a subprocess so the script's CLI-entry-point code
path is covered end-to-end. Exit codes:
  - 0 on successful command completion under the deadline
  - 124 on deadline exceeded (matching GNU `timeout`'s convention)
  - 2 on CLI usage errors (missing/invalid arguments)
  - whatever the wrapped command exited with, otherwise
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

# parents[5] -> tests/unit/meta -> unit -> tests -> backend -> apps -> repo
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_WRAPPER: Final[Path] = _REPO_ROOT / "infra" / "taskfile" / "with_timeout.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the wrapper as a subprocess with Python from the active venv."""
    return subprocess.run(
        [sys.executable, str(_WRAPPER), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_wrapper_script_is_present() -> None:
    assert _WRAPPER.is_file(), f"expected wrapper at {_WRAPPER}"


def test_wrapper_passes_through_successful_command() -> None:
    """A fast command under the deadline exits 0 and its stdout is forwarded."""
    result = _run("5", "--", sys.executable, "-c", "print('hello')")
    assert result.returncode == 0, result.stderr
    assert "hello" in result.stdout


def test_wrapper_forwards_non_zero_exit_from_wrapped_command() -> None:
    """If the wrapped command exits non-zero, that exit code propagates."""
    result = _run("5", "--", sys.executable, "-c", "import sys; sys.exit(7)")
    assert result.returncode == 7


def test_wrapper_times_out_and_exits_124() -> None:
    """If the wrapped command exceeds the deadline, exit 124 (GNU timeout convention)."""
    # 1-second deadline, command sleeps 5 seconds — deadline wins.
    result = _run("1", "--", sys.executable, "-c", "import time; time.sleep(5)")
    assert result.returncode == 124, (
        f"expected exit 124 on deadline exceeded; got {result.returncode}. "
        f"stderr: {result.stderr!r}"
    )
    assert "timeout" in result.stderr.lower()


def test_wrapper_rejects_missing_command() -> None:
    """Passing only the deadline without a wrapped command must error."""
    result = _run("5", "--")
    assert result.returncode == 2


def test_wrapper_rejects_non_positive_timeout() -> None:
    """A non-positive deadline is a CLI usage error."""
    result = _run("0", "--", sys.executable, "-c", "pass")
    assert result.returncode == 2


def test_wrapper_rejects_non_integer_timeout() -> None:
    """A non-integer deadline token is a CLI usage error."""
    result = _run("abc", "--", sys.executable, "-c", "pass")
    assert result.returncode == 2


def test_wrapper_without_double_dash_separator_still_works() -> None:
    """The `--` separator is optional; tokens after the seconds are the command.

    Keeping `--` optional avoids a needless tripping hazard in
    Taskfile cmds that would otherwise need shell-escaped separators.
    """
    result = _run("5", sys.executable, "-c", "print('ok')")
    assert result.returncode == 0
    assert "ok" in result.stdout

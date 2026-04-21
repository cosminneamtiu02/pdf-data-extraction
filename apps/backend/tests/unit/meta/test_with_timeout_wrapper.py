"""Unit tests for the `infra/taskfile/with_timeout.py` command wrapper.

Tests the wrapper as a subprocess so the script's CLI-entry-point code
path is covered end-to-end. Exit codes:
  - 0 on successful command completion under the deadline
  - 124 on deadline exceeded (matching GNU `timeout`'s convention)
  - 2 on CLI usage errors (missing/invalid arguments)
  - 128 + signum when the wrapper is cancelled by SIGINT / SIGTERM / SIGHUP
    (shell convention — matches how `timeout(1)` propagates signals)
  - whatever the wrapped command exited with, otherwise
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
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


def _spawn_wrapper_with_long_child() -> subprocess.Popen[str]:
    """Spawn the wrapper running a long-sleeping child that writes its PID.

    The child prints its own PID, then sleeps. Returning the Popen lets
    the test send a signal to the wrapper and then assert the child
    process group no longer exists. Using a unique marker on stdout
    lets the test race-free read the child's PID before signalling.
    """
    child_code = (
        "import os, sys, time;"
        "sys.stdout.write(f'CHILD_PID={os.getpid()}\\n');"
        "sys.stdout.flush();"
        "time.sleep(30)"
    )
    # Start the wrapper in its own process group so the test can
    # signal the wrapper directly without racing tty-routed signals
    # to the pytest runner itself.
    return subprocess.Popen(
        [sys.executable, str(_WRAPPER), "10", "--", sys.executable, "-u", "-c", child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _read_child_pid(proc: subprocess.Popen[str]) -> int:
    """Block until the wrapper's stdout emits the CHILD_PID=<n> marker."""
    assert proc.stdout is not None
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            continue
        if line.startswith("CHILD_PID="):
            return int(line.removeprefix("CHILD_PID=").strip())
    msg = "wrapper never emitted CHILD_PID= line within 5 s"
    raise AssertionError(msg)


def _pid_alive(pid: int) -> bool:
    """Return True iff the given PID is still a live, signalable process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A live-but-foreign process still counts as "alive" for leak detection.
        return True
    return True


def _assert_pid_reaped(pid: int, timeout_seconds: float = 5.0) -> None:
    """Poll until `pid` no longer exists, or fail after `timeout_seconds`."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    msg = f"child pid {pid} survived wrapper termination — process tree leaked"
    raise AssertionError(msg)


def test_wrapper_sigterm_reaps_child_process_tree() -> None:
    """SIGTERM to the wrapper must propagate to the child's process group.

    Without explicit signal handlers the wrapper would exit (because
    Python's default SIGTERM handler calls sys.exit) while the
    setsid'd child group kept running in the background, leaking a
    subprocess tree past the Taskfile's deadline. Assert the child
    process goes away after the wrapper receives SIGTERM.
    """
    if os.name != "posix":
        import pytest

        pytest.skip("setsid / killpg signal handling is POSIX-only")
    proc = _spawn_wrapper_with_long_child()
    try:
        child_pid = _read_child_pid(proc)
        assert _pid_alive(child_pid), "child should be alive before we signal the wrapper"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        _assert_pid_reaped(child_pid)
        # Shell convention: signal-caused exits report 128 + signum.
        assert proc.returncode == 128 + signal.SIGTERM, (
            f"expected exit code {128 + signal.SIGTERM} on SIGTERM, got {proc.returncode}. "
            f"stderr: {proc.stderr.read() if proc.stderr else ''!r}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_wrapper_sigint_reaps_child_process_tree() -> None:
    """SIGINT (Ctrl+C) to the wrapper must also reap the child process tree.

    KeyboardInterrupt would otherwise unwind the wrapper while the
    child kept running — the same leak failure mode as SIGTERM, just
    via the interactive-cancellation code path.
    """
    if os.name != "posix":
        import pytest

        pytest.skip("setsid / killpg signal handling is POSIX-only")
    proc = _spawn_wrapper_with_long_child()
    try:
        child_pid = _read_child_pid(proc)
        assert _pid_alive(child_pid), "child should be alive before we signal the wrapper"
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=10)
        _assert_pid_reaped(child_pid)
        assert proc.returncode == 128 + signal.SIGINT, (
            f"expected exit code {128 + signal.SIGINT} on SIGINT, got {proc.returncode}. "
            f"stderr: {proc.stderr.read() if proc.stderr else ''!r}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

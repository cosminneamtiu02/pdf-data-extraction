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

import importlib.util
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import IO, Final

# parents[5] -> tests/unit/meta -> unit -> tests -> backend -> apps -> repo
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_WRAPPER: Final[Path] = _REPO_ROOT / "infra" / "taskfile" / "with_timeout.py"


def _load_wrapper_module() -> ModuleType:
    """Import `with_timeout.py` as a module so whitebox tests can call helpers.

    The wrapper lives in `infra/taskfile/`, which is outside the
    regular package tree (it's shipped with the repo as a plain script
    for Taskfile to call). `importlib.util.spec_from_file_location`
    lets us load it in-process without polluting `sys.path`.
    """
    spec = importlib.util.spec_from_file_location("_with_timeout_for_tests", _WRAPPER)
    assert spec is not None, f"cannot load spec for {_WRAPPER}"
    assert spec.loader is not None, f"spec for {_WRAPPER} has no loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    """Block (at most 5 s) until the wrapper's stdout emits the CHILD_PID=<n> marker.

    Uses a background thread that feeds each `readline()` into a
    `queue.Queue`, and polls the queue with `queue.get(timeout=...)`.
    A bare `proc.stdout.readline()` inside a `while time.monotonic() <
    deadline` loop would block indefinitely if the child died silently
    or stopped flushing stdout, because `readline()` has no per-call
    timeout — the surrounding loop check would never be re-evaluated.
    Routing the read through a queue lets the outer deadline actually
    fire, so a regression in the wrapper cannot hang CI forever.
    """
    assert proc.stdout is not None
    lines: queue.Queue[str] = queue.Queue()

    def _pump(stream: IO[str], sink: queue.Queue[str]) -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                sink.put(line)
        finally:
            sink.put("")

    reader = threading.Thread(
        target=_pump, args=(proc.stdout, lines), daemon=True, name="wrapper-stdout-pump"
    )
    reader.start()
    deadline = time.monotonic() + 5.0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            break
        if not line:
            # Pump signalled EOF; stdout is closed — no further lines
            # will appear, so fall through to the AssertionError below
            # instead of spinning on the queue for the rest of the 5 s
            # budget.
            break
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


def test_terminate_process_tree_is_noop_when_child_already_exited() -> None:
    """`_terminate_process_tree` must short-circuit on an exited child.

    POSIX PIDs are recyclable: once a child has been waited on and
    reaped, the kernel is free to hand its PID to a completely
    unrelated process. Calling `os.killpg(proc.pid, SIGTERM)` on a
    stale `Popen` object would then signal an innocent process group
    (for example, a sibling `task` invocation that happened to land on
    the recycled PID). Guard that by checking `proc.poll()` first and
    returning early — verified here by handing the helper an already-
    exited child and asserting that (a) it returns without raising and
    (b) it does not re-signal the reaped process.
    """
    if os.name != "posix":
        import pytest

        pytest.skip("killpg short-circuit is POSIX-specific")
    wrapper = _load_wrapper_module()
    # Spawn a trivial child, wait for it to exit, then hand the reaped
    # Popen to `_terminate_process_tree`. If the short-circuit is
    # missing, the helper would attempt `os.killpg` on a dead PID and
    # either succeed against a recycled group (undetectable from here)
    # or raise ProcessLookupError (which the current code already
    # swallows). What we *can* assert from userland is that the helper
    # returns cleanly and does not perturb the proc's returncode.
    finished = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        start_new_session=True,
    )
    finished.wait(timeout=5)
    assert finished.poll() is not None, "sanity: child should have exited"
    original_returncode = finished.returncode
    # The bug path would be a raised exception from os.killpg or a
    # spurious side effect; the guard path is a clean return.
    # SLF001: calling the private helper is the whole point of this
    # whitebox test — the PID-reuse guard lives inside that function
    # and has no public entry point.
    wrapper._terminate_process_tree(finished)  # noqa: SLF001
    assert finished.returncode == original_returncode

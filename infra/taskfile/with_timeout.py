"""Portable per-command timeout wrapper for Taskfile.yml (issue #357).

go-task's schema has no per-task `timeout:` attribute
(https://taskfile.dev/docs/reference/schema), and GNU coreutils
`timeout(1)` is not installed by default on macOS dev machines.
Wrapping every long-running command through this zero-dependency
Python 3.13 stdlib script gives us cross-platform deadlines without
adding a new external dependency.

CLI:
    python3 with_timeout.py <seconds> [--] <command> [args...]

Exit codes (aligned with GNU `timeout(1)` where applicable):
    - 124 : command exceeded the deadline
    - 2   : CLI usage error (missing/invalid args)
    - N   : any other exit code is whatever the wrapped command exited with
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import NoReturn

# GNU timeout(1) exits 124 when the deadline fires. Mirror that so
# Taskfile consumers can special-case the timeout case without parsing
# stderr.
_TIMEOUT_EXIT_CODE = 124
_USAGE_EXIT_CODE = 2
_USAGE = (
    "usage: with_timeout.py <seconds> [--] <command> [args...]\n"
    "  seconds: positive integer wall-clock deadline\n"
    "  command: argv to execute (may omit -- when unambiguous)\n"
)


def _die_usage(msg: str) -> NoReturn:
    sys.stderr.write(f"{msg}\n{_USAGE}")
    sys.exit(_USAGE_EXIT_CODE)


def _parse_args(argv: list[str]) -> tuple[int, list[str]]:
    """Return (seconds, command_argv). Exits with code 2 on usage error."""
    if len(argv) < 2:
        _die_usage("missing arguments")
    raw_seconds = argv[0]
    try:
        seconds = int(raw_seconds)
    except ValueError:
        _die_usage(f"seconds must be an integer, got {raw_seconds!r}")
    if seconds <= 0:
        _die_usage(f"seconds must be positive, got {seconds}")
    remainder = argv[1:]
    # Strip a single leading `--` separator if present.
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    if not remainder:
        _die_usage("missing command to execute")
    return seconds, remainder


def _run_with_deadline(seconds: int, command: list[str]) -> int:
    """Run `command` with a wall-clock deadline of `seconds`. Return the exit code.

    On POSIX, start the child in its own process group so we can
    terminate the whole subtree on deadline expiration — a pytest /
    uv / docker shell that forks helpers would otherwise leak those
    helpers past the deadline. On Windows, fall back to the Popen's
    built-in kill which uses TerminateProcess on the root process.
    """
    preexec_fn = os.setsid if os.name == "posix" else None
    # S603 / S607: we deliberately execute arbitrary argv passed by
    # the Taskfile author. Shell=False (the default) keeps us out of
    # shell-injection territory; the Taskfile is a trusted input.
    proc = subprocess.Popen(  # noqa: S603
        command,
        preexec_fn=preexec_fn,
    )
    try:
        return proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)
        sys.stderr.write(
            f"with_timeout.py: command exceeded {seconds}s deadline; terminated.\n"
        )
        return _TIMEOUT_EXIT_CODE


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Terminate `proc` and (on POSIX) its whole process group.

    Two-phase shutdown: SIGTERM to allow graceful cleanup, then SIGKILL
    after a short grace window if the process hasn't exited. The grace
    window is intentionally short (5 s) so CI doesn't stall beyond the
    user-declared deadline by more than a small constant.
    """
    grace_seconds = 5
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        proc.terminate()
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        proc.kill()
    # Reap the zombie; swallow a second TimeoutExpired because at this
    # point we've already sent SIGKILL and can't do anything more.
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    seconds, command = _parse_args(args)
    return _run_with_deadline(seconds, command)


if __name__ == "__main__":
    sys.exit(main())

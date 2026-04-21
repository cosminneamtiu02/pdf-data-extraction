"""Unit tests for ``scripts._stream_aware_argument_parser`` (issue #318, PR #474).

Pins the contract that ``StreamAwareArgumentParser`` routes argparse's
internal ``_print_message`` writes to the caller-supplied ``out`` / ``err``
streams directly, without temporarily rebinding ``sys.stdout`` /
``sys.stderr`` via ``contextlib.redirect_*``. The thread-safe contract
matters because the benchmark script can be invoked from contexts where
other threads may be writing to the real stderr concurrently — a global
rebind would divert those writes onto the benchmark's buffer and
reintroduce the kind of leakage issue #326 was trying to avoid.
"""

from __future__ import annotations

import io
import sys

import pytest

from scripts._stream_aware_argument_parser import StreamAwareArgumentParser


def test_stream_aware_parser_routes_error_to_injected_err_stream() -> None:
    """``error()`` writes the formatted argparse message to the injected err."""
    err_stream = io.StringIO()
    out_stream = io.StringIO()
    parser = StreamAwareArgumentParser(
        prog="demo",
        out_stream=out_stream,
        err_stream=err_stream,
    )
    parser.add_argument("--iterations", type=int)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--iterations", "banana"])

    assert exc_info.value.code == 2
    err_text = err_stream.getvalue()
    assert "--iterations" in err_text
    assert "banana" in err_text
    # Nothing on the out stream — errors go to err.
    assert out_stream.getvalue() == ""


def test_stream_aware_parser_routes_help_to_injected_out_stream() -> None:
    """``print_help()`` writes the help banner to the injected out stream."""
    err_stream = io.StringIO()
    out_stream = io.StringIO()
    parser = StreamAwareArgumentParser(
        prog="demo",
        out_stream=out_stream,
        err_stream=err_stream,
    )
    parser.add_argument("--iterations", type=int, help="iteration count")

    parser.print_help()

    out_text = out_stream.getvalue()
    assert "--iterations" in out_text
    assert "iteration count" in out_text
    # Nothing on the err stream — help goes to out.
    assert err_stream.getvalue() == ""


def test_stream_aware_parser_help_flag_exits_zero_and_writes_to_injected_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``parse_args(["--help"])`` exits 0 and writes banner to injected out."""
    err_stream = io.StringIO()
    out_stream = io.StringIO()
    parser = StreamAwareArgumentParser(
        prog="demo",
        out_stream=out_stream,
        err_stream=err_stream,
    )
    parser.add_argument("--iterations", type=int)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])

    assert exc_info.value.code == 0
    out_text = out_stream.getvalue()
    assert "--iterations" in out_text

    # Process-global stdout/stderr were not touched.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_stream_aware_parser_does_not_rebind_process_sys_stderr_on_error() -> None:
    """Parsing a bad arg never reassigns ``sys.stderr`` or ``sys.stdout``.

    Thread-safety pin: unlike the previous
    ``contextlib.redirect_stderr(err_stream)`` approach, this subclass
    writes to the injected buffer directly and must leave the process-global
    stream references untouched for the duration of the call.
    """
    saved_stderr = sys.stderr
    saved_stdout = sys.stdout

    err_stream = io.StringIO()
    out_stream = io.StringIO()
    parser = StreamAwareArgumentParser(
        prog="demo",
        out_stream=out_stream,
        err_stream=err_stream,
    )
    parser.add_argument("--iterations", type=int)

    with pytest.raises(SystemExit):
        parser.parse_args(["--iterations", "banana"])

    # Process-global references are the exact same objects as before.
    assert sys.stderr is saved_stderr
    assert sys.stdout is saved_stdout


def test_stream_aware_parser_does_not_rebind_process_sys_stdout_on_help() -> None:
    """``print_help()`` never reassigns ``sys.stdout`` or ``sys.stderr``."""
    saved_stderr = sys.stderr
    saved_stdout = sys.stdout

    err_stream = io.StringIO()
    out_stream = io.StringIO()
    parser = StreamAwareArgumentParser(
        prog="demo",
        out_stream=out_stream,
        err_stream=err_stream,
    )
    parser.add_argument("--iterations", type=int)

    parser.print_help()

    assert sys.stderr is saved_stderr
    assert sys.stdout is saved_stdout


def test_stream_aware_parser_defaults_to_process_streams_when_no_override(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Constructing without explicit streams falls back to ``sys.stdout``/``sys.stderr``.

    Preserves backward compatibility with argparse's default behavior so
    ``StreamAwareArgumentParser()`` with no extra kwargs behaves like a
    plain ``ArgumentParser``. This matters for any code path that might
    reach the parser before ``main`` wires up an injected buffer.
    """
    parser = StreamAwareArgumentParser(prog="demo")
    parser.add_argument("--iterations", type=int)

    parser.print_help()

    captured = capsys.readouterr()
    assert "--iterations" in captured.out

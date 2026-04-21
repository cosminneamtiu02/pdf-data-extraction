"""StreamAwareArgumentParser — argparse subclass that honors injected streams (issue #318, PR #474).

The benchmark script's ``main(out=, err=)`` contract (issue #326) promises
that every byte of CLI output — report text, operator-facing error
messages, usage banners, and help text — lands on the caller-supplied
``out`` / ``err`` buffers rather than on the process-global
``sys.stdout`` / ``sys.stderr`` streams. Plain ``argparse.ArgumentParser``
breaks that contract because its internal ``_print_message`` method writes
to ``sys.stdout`` (for ``--help`` / ``--usage``) and ``sys.stderr`` (for
parse/type errors and their usage banner) directly.

Earlier revisions worked around this with
``contextlib.redirect_stderr(err_stream)`` wrapped around
``parser.parse_args(argv)``. That approach works single-threaded but is
not thread-safe: ``contextlib.redirect_stderr`` temporarily rebinds
``sys.stderr`` at the process level for the duration of the ``with``
block, so any concurrent write to ``sys.stderr`` from another thread
(e.g. structlog emitting a log line during the parse) would silently
land on the benchmark's injected buffer instead of the real stderr.
That reintroduces the kind of stream leakage issue #326 was trying to
eliminate (PR #474 round-4 review feedback).

This subclass replaces that mechanism. ``_print_message`` is overridden
to route argparse's writes directly onto the caller-supplied buffers
based on whether argparse asked for ``sys.stderr`` (route to
``err_stream``) or ``sys.stdout`` (route to ``out_stream``), without
touching the process-global stream references at any point. The thread-
safety property is verified by
``test_stream_aware_parser_does_not_rebind_process_sys_stderr_on_error``
and ``test_main_error_does_not_mutate_process_sys_stderr_during_parse``.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import TextIO


class StreamAwareArgumentParser(argparse.ArgumentParser):
    """ArgumentParser subclass that writes to injected out/err streams.

    Parameters
    ----------
    out_stream
        Target for help / usage output (argparse's default is ``sys.stdout``).
        When ``None``, falls back to ``sys.stdout`` at write time so the
        subclass is behaviorally identical to a plain ``ArgumentParser``
        for callers that never supply a buffer.
    err_stream
        Target for error output (argparse's default is ``sys.stderr``).
        When ``None``, falls back to ``sys.stderr`` at write time for the
        same reason.

    All other constructor kwargs are forwarded verbatim to
    :class:`argparse.ArgumentParser`.

    Implementation
    --------------
    Argparse funnels every internal write through ``_print_message(message,
    file)``. For ``print_help`` / ``print_usage`` the ``file`` argument is
    ``sys.stdout``; for ``error`` / ``exit`` it is ``sys.stderr``. The
    override inspects ``file`` and swaps in the injected buffer when the
    caller requested the matching process stream. This leaves the process-
    global references untouched (unlike ``contextlib.redirect_*``), so
    concurrent writes to the real stderr/stdout from other threads are not
    diverted.
    """

    def __init__(
        self,
        *args: Any,
        out_stream: TextIO | None = None,
        err_stream: TextIO | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._out_stream: TextIO | None = out_stream
        self._err_stream: TextIO | None = err_stream

    def _print_message(self, message: str, file: IO[str] | None = None) -> None:  # type: ignore[override]  # argparse stdlib is not type-annotated strictly; we widen to str return-None to match
        """Route argparse's internal writes to the injected buffers.

        Mirrors the stdlib implementation's fallback behavior: if
        ``message`` is empty, do nothing; otherwise write to the resolved
        target and swallow ``AttributeError`` / ``OSError`` the same way
        the parent does (e.g. when the target file does not implement
        ``write``). The routing rule:

        * ``file is sys.stderr`` — route to ``err_stream`` when injected,
          else write to ``sys.stderr`` directly.
        * ``file is sys.stdout`` — route to ``out_stream`` when injected,
          else write to ``sys.stdout`` directly.
        * anything else (unlikely, but the stdlib allows it) — pass
          through to the caller-supplied ``file`` unchanged so custom
          callers keep working.
        """
        if not message:
            return

        target: IO[str] | TextIO
        if file is sys.stderr:
            target = self._err_stream if self._err_stream is not None else sys.stderr
        elif file is sys.stdout or file is None:
            target = self._out_stream if self._out_stream is not None else sys.stdout
        else:
            target = file

        # Mirrors stdlib ArgumentParser._print_message, which silently drops
        # write failures rather than raising during error reporting.
        with contextlib.suppress(AttributeError, OSError):
            target.write(message)

"""Unit tests for ``scripts.benchmark`` — local latency benchmark script.

Covers the sixteen unit scenarios in PDFX-E007-F005: percentile computation
(happy path, single value, empty), report formatting (per-fixture table,
memory section, NFR comparison), fixture discovery (all present, one
missing, all missing), CLI parsing (--help, defaults, overrides, validation),
warm-up discard, and pyright strict pass.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.benchmark import (
    BenchResults,
    FixtureBenchResult,
    MemorySnapshot,
    ModeBenchResult,
    compute_percentile,
    discard_warmup,
    discover_fixtures,
    format_report,
    main,
    parse_args,
)

# ---------------------------------------------------------------------------
# Percentile computation
# ---------------------------------------------------------------------------


def test_compute_percentile_ten_values_returns_correct_p50_and_p95() -> None:
    """compute_percentile with [1..10] returns p50=5.5 and p95=9.55."""
    latencies = [float(i) for i in range(1, 11)]
    assert compute_percentile(latencies, 50) == pytest.approx(5.5)
    assert compute_percentile(latencies, 95) == pytest.approx(9.55)


def test_compute_percentile_single_value_returns_that_value() -> None:
    """compute_percentile with [42.0] returns 42.0 for both p50 and p95."""
    assert compute_percentile([42.0], 50) == pytest.approx(42.0)
    assert compute_percentile([42.0], 95) == pytest.approx(42.0)


def test_compute_percentile_empty_list_raises_value_error() -> None:
    """compute_percentile with [] raises ValueError."""
    with pytest.raises(ValueError, match="empty"):
        compute_percentile([], 50)


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def _make_mode_result(latencies: list[float]) -> ModeBenchResult:
    return ModeBenchResult(latencies=latencies)


def _make_fixture_result(name: str) -> FixtureBenchResult:
    return FixtureBenchResult(
        fixture_name=name,
        modes={
            "JSON_ONLY": _make_mode_result([5.0, 6.0, 7.0]),
            "PDF_ONLY": _make_mode_result([8.0, 9.0, 10.0]),
            "BOTH": _make_mode_result([11.0, 12.0, 13.0]),
        },
    )


def _make_results() -> BenchResults:
    return BenchResults(
        fixtures=[
            _make_fixture_result("native_invoice_10p"),
            _make_fixture_result("scanned_invoice_10p"),
            _make_fixture_result("table_heavy_5p"),
        ],
        memory=MemorySnapshot(rss_before_mb=100.0, rss_after_mb=120.0),
    )


def test_format_report_contains_per_fixture_latency_table() -> None:
    """Report output contains rows for each fixture x mode with p50/p95."""
    results = _make_results()
    report = format_report(results)

    for name in ("native_invoice_10p", "scanned_invoice_10p", "table_heavy_5p"):
        assert name in report

    for mode in ("JSON_ONLY", "PDF_ONLY", "BOTH"):
        assert mode in report

    # Should contain statistical labels
    assert "p50" in report.lower() or "P50" in report
    assert "p95" in report.lower() or "P95" in report


def test_format_report_contains_memory_section() -> None:
    """Report includes RSS before, RSS after, and delta in MB."""
    results = _make_results()
    report = format_report(results)

    assert "100.0" in report or "100.00" in report  # RSS before
    assert "120.0" in report or "120.00" in report  # RSS after
    assert "20.0" in report or "20.00" in report  # delta


def test_format_report_contains_nfr_comparison() -> None:
    """Report includes NFR comparison with targets and pass/fail indicators."""
    results = _make_results()
    report = format_report(results)

    # NFR targets section header
    assert "NFR" in report or "Target" in report
    # Latency pass/fail rows for native and scanned
    assert "PASS" in report or "FAIL" in report
    assert "Native Invoice 10P" in report.title() or "native_invoice_10p" in report.lower()


def test_format_report_contains_annotation_overhead() -> None:
    """Report includes annotation overhead (PDF_ONLY p50 - JSON_ONLY p50) per fixture."""
    results = _make_results()
    report = format_report(results)

    # Annotation overhead row should exist
    assert "Annot. Overhead" in report or "annotation" in report.lower()
    # With JSON_ONLY=[5,6,7] (p50=6) and PDF_ONLY=[8,9,10] (p50=9), overhead=3.0
    # NFR-006 target is 2.0s, so this should FAIL
    assert "FAIL" in report


def test_format_report_service_rss_with_pid() -> None:
    """Report includes service RSS pass/fail when service_pid was provided."""
    results = BenchResults(
        fixtures=[_make_fixture_result("native_invoice_10p")],
        memory=MemorySnapshot(
            rss_before_mb=50.0,
            rss_after_mb=55.0,
            service_rss_before_mb=800.0,
            service_rss_after_mb=850.0,
        ),
    )
    report = format_report(results)

    assert "Service Memory" in report
    assert "800.00" in report
    assert "850.00" in report
    assert "NFR-008" in report
    assert "PASS" in report  # 800 MB (idle/pre-run) < 1500 MB target


def test_format_report_service_rss_without_pid() -> None:
    """Report shows skip message when --service-pid was not provided."""
    results = _make_results()
    report = format_report(results)

    assert "service-pid not provided" in report.lower() or "skipped" in report.lower()


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------

EXPECTED_FIXTURE_FILES = [
    "native_invoice_10p.pdf",
    "scanned_invoice_10p.pdf",
    "table_heavy_5p.pdf",
]

EXPECTED_FIXTURE_STEMS = [
    "native_invoice_10p",
    "scanned_invoice_10p",
    "table_heavy_5p",
]


def test_discover_fixtures_finds_all_three(tmp_path: Path) -> None:
    """discover_fixtures returns FixtureInfo with stem names for all three PDFs."""
    for name in EXPECTED_FIXTURE_FILES:
        (tmp_path / name).write_bytes(b"%PDF-1.4 stub")

    fixtures = discover_fixtures(tmp_path)
    assert len(fixtures) == 3
    names = {f.name for f in fixtures}
    assert names == set(EXPECTED_FIXTURE_STEMS)


def test_discover_fixtures_missing_one_names_missing_file(tmp_path: Path) -> None:
    """discover_fixtures with one missing file raises error naming it."""
    (tmp_path / "native_invoice_10p.pdf").write_bytes(b"%PDF-1.4 stub")
    (tmp_path / "table_heavy_5p.pdf").write_bytes(b"%PDF-1.4 stub")

    with pytest.raises(FileNotFoundError, match=r"scanned_invoice_10p\.pdf"):
        discover_fixtures(tmp_path)


def test_discover_fixtures_empty_dir_lists_all_missing(tmp_path: Path) -> None:
    """discover_fixtures with empty dir names all three missing files."""
    with pytest.raises(FileNotFoundError) as exc_info:
        discover_fixtures(tmp_path)

    msg = str(exc_info.value)
    for name in EXPECTED_FIXTURE_FILES:
        assert name in msg


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


def test_parse_args_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """--help exits 0 and shows --url, --iterations, --fixtures-dir."""
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--help"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--url" in captured.out
    assert "--iterations" in captured.out
    assert "--fixtures-dir" in captured.out
    assert "--service-pid" in captured.out


def test_parse_args_defaults() -> None:
    """No args produces config with iterations=10 and default URL."""
    config = parse_args([])
    assert config.iterations == 10
    assert "localhost" in config.url or "127.0.0.1" in config.url


def test_parse_args_url_override() -> None:
    """--url overrides the default base URL."""
    config = parse_args(["--url", "http://custom:8000"])
    assert config.url == "http://custom:8000"


def test_parse_args_iterations_override() -> None:
    """--iterations 5 sets iterations to 5."""
    config = parse_args(["--iterations", "5"])
    assert config.iterations == 5


def test_parse_args_iterations_zero_exits_nonzero() -> None:
    """--iterations 0 exits non-zero because iterations must be positive."""
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--iterations", "0"])

    assert exc_info.value.code != 0


def test_parse_args_reads_bench_env_vars_via_pydantic_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """BENCH_* env vars flow through BenchmarkSettings into parse_args defaults.

    Regression guard for issue #237: the script previously read env vars via
    ``os.environ.get`` (CLAUDE.md-forbidden). After the refactor they flow
    through :class:`scripts._benchmark_settings.BenchmarkSettings`, so this
    test pins the wiring by exercising every BENCH_* variable at once.

    Extended for issue #275 to also cover ``BENCH_WARMUP`` / ``BENCH_TIMEOUT``.
    """
    fixtures_dir = tmp_path / "bench-pdfs"
    monkeypatch.setenv("BENCH_URL", "http://example:9090")
    monkeypatch.setenv("BENCH_ITERATIONS", "3")
    monkeypatch.setenv("BENCH_FIXTURES_DIR", str(fixtures_dir))
    monkeypatch.setenv("BENCH_SKILL_NAME", "receipt")
    monkeypatch.setenv("BENCH_SKILL_VERSION", "2")
    monkeypatch.setenv("BENCH_SERVICE_PID", "1234")
    monkeypatch.setenv("BENCH_WARMUP", "3")
    monkeypatch.setenv("BENCH_TIMEOUT", "75.5")

    config = parse_args([])

    assert config.url == "http://example:9090"
    assert config.iterations == 3
    assert config.fixtures_dir == fixtures_dir
    assert config.skill_name == "receipt"
    assert config.skill_version == "2"
    assert config.service_pid == 1234
    assert config.warmup == 3
    assert config.timeout == 75.5


def test_parse_args_cli_warmup_and_timeout_override_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--warmup`` / ``--timeout`` on the CLI win over ``BENCH_*`` env values.

    Regression guard for issue #275: after env-parity was restored for these
    two knobs, the CLI must still take precedence over env — matching
    pydantic-settings' documented init-kwargs > env source priority and the
    behaviour of the other BENCH_*-backed flags.
    """
    monkeypatch.setenv("BENCH_WARMUP", "2")
    monkeypatch.setenv("BENCH_TIMEOUT", "30.0")

    config = parse_args(["--warmup", "7", "--timeout", "45"])

    assert config.warmup == 7
    assert config.timeout == 45.0


def test_parse_args_negative_warmup_exits_nonzero() -> None:
    """``--warmup -1`` exits non-zero via pydantic Field(ge=0)."""
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--warmup", "-1"])
    assert exc_info.value.code != 0


def test_parse_args_zero_timeout_exits_nonzero() -> None:
    """``--timeout 0`` exits non-zero via pydantic Field(gt=0)."""
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--timeout", "0"])
    assert exc_info.value.code != 0


def test_parse_args_empty_bench_service_pid_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty ``BENCH_SERVICE_PID`` (as passed by Taskfile) parses as ``None``."""
    monkeypatch.setenv("BENCH_SERVICE_PID", "")
    config = parse_args([])
    assert config.service_pid is None


def test_parse_args_invalid_bench_env_exits_two_with_stderr_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Invalid ``BENCH_*`` env values produce a concise stderr message + exit 2.

    ``BenchmarkSettings()`` can raise ``pydantic.ValidationError`` on bad env
    input; ``parse_args`` must convert that into an argparse-style operator
    error (stderr + ``SystemExit(2)``) rather than letting it bubble up as a
    traceback. Regression guard for the Copilot review feedback on PR #246:
    the pre-refactor ``_safe_int_env`` branch gave exit code 2 + stderr; the
    pydantic-settings replacement must preserve that contract.
    """
    monkeypatch.setenv("BENCH_ITERATIONS", "banana")

    with pytest.raises(SystemExit) as exc_info:
        parse_args([])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "BENCH_ITERATIONS" in captured.err or "iterations" in captured.err.lower()
    # No traceback or Pydantic internals leaked to stderr
    assert "Traceback" not in captured.err
    assert "ValidationError" not in captured.err


def test_parse_args_cli_iterations_overrides_invalid_bench_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``--iterations`` flag wins over a malformed ``BENCH_ITERATIONS``.

    Regression guard for the PR #246 round-2 Copilot feedback: previously
    ``BenchmarkSettings()`` was constructed *before* argv was parsed, so a bad
    ``BENCH_*`` value aborted the script even if the operator had supplied a
    valid overriding flag on the same command line. ``parse_args`` must parse
    argv first, collect explicit CLI overrides, and pass them as init kwargs
    to ``BenchmarkSettings(**cli_overrides)`` so the env source is only used
    for fields the CLI did not cover — matching pydantic-settings' documented
    priority ordering (CLI > init kwargs > env > defaults).
    """
    monkeypatch.setenv("BENCH_ITERATIONS", "banana")

    config = parse_args(["--iterations", "5"])

    assert config.iterations == 5


def test_parse_args_service_pid_zero_normalizes_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``service_pid=0`` (from env or CLI) normalizes to ``None`` in BenchConfig.

    PID ``0`` is never a valid target process (POSIX reserves it for the
    current process group in ``kill(2)`` / signal semantics, and ``ps -p 0``
    produces no output). The pre-refactor ``_safe_int_env`` path collapsed 0
    to ``None`` via ``... or None``; this test pins that semantics through
    the pydantic-settings replacement so an operator who sets
    ``BENCH_SERVICE_PID=0`` or ``--service-pid 0`` does not carry a dangling
    invalid PID through the config.
    """
    monkeypatch.setenv("BENCH_SERVICE_PID", "0")
    assert parse_args([]).service_pid is None

    monkeypatch.delenv("BENCH_SERVICE_PID", raising=False)
    assert parse_args(["--service-pid", "0"]).service_pid is None


# ---------------------------------------------------------------------------
# main(out=, err=) stream-injection contract (issue #318)
# ---------------------------------------------------------------------------


def test_main_missing_fixtures_writes_error_to_injected_err_not_process_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main(out=, err=) routes FileNotFoundError to injected err; no sys.stderr leakage.

    End-to-end guard for issue #318: confirms that when ``main`` rejects a
    missing-fixtures run, the error message lands in the caller-supplied
    ``err`` buffer rather than on the process-global ``sys.stderr``. Uses
    ``capsys`` as a tripwire — if any byte leaks to the real streams, the
    assertion on ``captured.err`` / ``captured.out`` fails.
    """
    fixtures_dir = tmp_path / "empty"
    fixtures_dir.mkdir()

    fake_out = io.StringIO()
    fake_err = io.StringIO()

    code = main(
        ["--fixtures-dir", str(fixtures_dir), "--iterations", "1"],
        out=fake_out,
        err=fake_err,
    )

    assert code != 0
    err_text = fake_err.getvalue()
    # All three expected fixture names should be named in the error message.
    assert "native_invoice_10p.pdf" in err_text
    assert "scanned_invoice_10p.pdf" in err_text
    assert "table_heavy_5p.pdf" in err_text

    # No leakage to process-global streams. The test keys on the specific
    # fixture tokens rather than asserting ``captured.err == ""`` so unrelated
    # pytest/structlog output does not false-trip the tripwire. Checks BOTH
    # ``captured.err`` and ``captured.out`` — a regression that routed the
    # error to process-global stdout instead of stderr would slip past a
    # stderr-only check.
    captured = capsys.readouterr()
    assert "native_invoice_10p.pdf" not in captured.err
    assert "scanned_invoice_10p.pdf" not in captured.err
    assert "table_heavy_5p.pdf" not in captured.err
    assert "native_invoice_10p.pdf" not in captured.out
    assert "scanned_invoice_10p.pdf" not in captured.out
    assert "table_heavy_5p.pdf" not in captured.out
    assert fake_out.getvalue() == ""


def test_main_invalid_env_var_writes_error_to_injected_err_not_process_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main(out=, err=) routes BenchmarkSettings ValidationError to injected err.

    Regression guard for issue #318: the ``BenchmarkSettings(**cli_overrides)``
    call inside ``parse_args`` must surface bad ``BENCH_*`` env values through
    the same injected ``err`` stream that ``main`` uses for all other error
    output. Before the fix, ``parse_args`` wrote directly to
    ``sys.stderr.write(...)`` which bypassed the caller's stream and leaked
    onto the process-global ``sys.stderr`` (racing with pytest capture and
    defeating the purpose of the kwarg).
    """
    monkeypatch.setenv("BENCH_ITERATIONS", "banana")

    fake_out = io.StringIO()
    fake_err = io.StringIO()

    code = main([], out=fake_out, err=fake_err)

    assert code == 2
    err_text = fake_err.getvalue()
    assert "BENCH_ITERATIONS" in err_text or "iterations" in err_text.lower()

    # No leakage to process-global stderr/stdout — the whole point of the
    # injected stream kwargs. ``BENCH_ITERATIONS`` is the operator-facing
    # token unique to this failure path (pydantic-settings surfaces it
    # verbatim), so checking it alone is specific enough; a broader
    # ``"iterations"`` match would false-trip on any unrelated warning/log
    # line that happens to use the word. Both ``captured.err`` and
    # ``captured.out`` are checked so a regression that writes the error to
    # process-global stdout instead of stderr is still caught.
    captured = capsys.readouterr()
    assert "BENCH_ITERATIONS" not in captured.err
    assert "BENCH_ITERATIONS" not in captured.out
    assert fake_out.getvalue() == ""


def test_main_invalid_cli_flag_writes_error_to_injected_err_not_process_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main(out=, err=) routes bad ``--iterations`` flag to injected err, not sys.stderr.

    ``--iterations 0`` trips the pydantic ``Field(gt=0)`` invariant on
    :class:`BenchmarkSettings`. As with the env-var variant, the operator
    error message must be visible to the caller via the injected ``err``
    buffer and must not leak to the process-global ``sys.stderr``.
    """
    fake_out = io.StringIO()
    fake_err = io.StringIO()

    code = main(["--iterations", "0"], out=fake_out, err=fake_err)

    assert code == 2
    err_text = fake_err.getvalue()
    assert "--iterations" in err_text or "iterations" in err_text.lower()

    # Guard against stream leakage using the operator-facing token
    # ``--iterations`` rather than the bare word ``iterations`` — the bare
    # word could appear in any unrelated stderr/stdout log line and false-trip
    # the assertion even when the benchmark error correctly went to
    # ``fake_err``. Both ``captured.err`` and ``captured.out`` are checked so
    # a regression that misroutes the error onto process-global stdout cannot
    # slip past a stderr-only tripwire.
    captured = capsys.readouterr()
    assert "--iterations" not in captured.err
    assert "--iterations" not in captured.out
    assert fake_out.getvalue() == ""


def test_main_argparse_type_error_writes_error_to_injected_err_not_process_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main(out=, err=) routes argparse type-callable errors to injected err.

    Regression guard for issue #318 (PR #474 round-3 feedback): ``--iterations
    banana`` fails inside argparse's own ``type=int`` callable *before*
    pydantic validation runs. Argparse emits its "invalid int value" message
    through the parser's message-printing path, so our stream-aware parser
    must route that output to the injected ``err`` stream rather than letting
    it leak to process-global stderr. This test pins that contract so the
    argparse leak cannot silently regress.
    """
    fake_out = io.StringIO()
    fake_err = io.StringIO()

    code = main(["--iterations", "banana"], out=fake_out, err=fake_err)

    assert code == 2
    err_text = fake_err.getvalue()
    # argparse's own message format: "argument --iterations: invalid int value: 'banana'"
    assert "--iterations" in err_text
    assert "banana" in err_text

    # No leakage to process-global stderr/stdout — the whole point of the
    # injected err/out kwargs. ``banana`` is a unique token that can only
    # come from the failed argparse type-callable, so it is specific enough
    # to guard against leakage without risking false trips from unrelated
    # stderr/stdout log lines. Both streams are checked so a regression
    # that writes the error onto process-global stdout is also caught.
    captured = capsys.readouterr()
    assert "banana" not in captured.err
    assert "--iterations" not in captured.err
    assert "banana" not in captured.out
    assert "--iterations" not in captured.out
    assert fake_out.getvalue() == ""


def test_main_help_writes_help_text_to_injected_out_not_process_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main(out=, err=) routes ``--help`` text to injected ``out``, not sys.stdout.

    Regression guard for PR #474 round-4 feedback: argparse's ``print_help``
    writes to ``sys.stdout`` by default, so ``main(["--help"], out=StringIO())``
    previously leaked the help banner onto the process-global stdout even
    though the caller injected a buffer. The custom
    ``StreamAwareArgumentParser`` threads ``out_stream`` into
    ``parser.print_help`` so ``--help`` honors the kwarg like every other
    CLI output path.
    """
    fake_out = io.StringIO()
    fake_err = io.StringIO()

    code = main(["--help"], out=fake_out, err=fake_err)

    assert code == 0
    out_text = fake_out.getvalue()
    # argparse's help banner contains the ``--iterations`` flag description.
    assert "--iterations" in out_text
    assert "--url" in out_text
    assert "--fixtures-dir" in out_text

    # No leakage to process-global stdout — the whole point of the out kwarg.
    # ``--iterations`` is the operator-facing token unique to the help banner;
    # checking it on both streams guards against leakage in either direction
    # without relying on the brittle "empty output" assertion.
    captured = capsys.readouterr()
    assert "--iterations" not in captured.out
    assert "--iterations" not in captured.err
    assert fake_err.getvalue() == ""


def test_main_error_does_not_mutate_process_sys_stderr_during_parse(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main error paths route through the parser without rebinding sys.stderr.

    Regression guard for PR #474 round-4 feedback: the previous implementation
    wrapped ``parser.parse_args`` in ``contextlib.redirect_stderr(err_stream)``,
    which temporarily reassigned ``sys.stderr`` for the duration of the call.
    That reassignment is not thread-safe — any concurrent logging on another
    thread would have landed on ``err_stream`` instead of the real stderr.
    The :class:`StreamAwareArgumentParser` replacement routes argparse's
    writes directly onto the injected buffers without touching
    ``sys.stderr`` / ``sys.stdout`` at all. This test pins that invariant:
    ``sys.stderr`` must be the same object on both sides of a failing
    ``main`` call.
    """
    saved_stderr = sys.stderr
    saved_stdout = sys.stdout

    fake_out = io.StringIO()
    fake_err = io.StringIO()

    code = main(["--iterations", "banana"], out=fake_out, err=fake_err)

    # sys.stderr / sys.stdout are the exact same object references as before
    # the call — no ``contextlib.redirect_*`` temporarily rebinding them.
    assert sys.stderr is saved_stderr
    assert sys.stdout is saved_stdout

    # And the argparse error still routed to the injected buffer (the test
    # above covers that positively; here we check code propagation).
    assert code == 2
    assert "banana" in fake_err.getvalue()

    # Tripwire: no process-global stream leakage either.
    captured = capsys.readouterr()
    assert "banana" not in captured.err
    assert "banana" not in captured.out


# ---------------------------------------------------------------------------
# Warm-up discard
# ---------------------------------------------------------------------------


def test_warmup_discard_removes_first_value() -> None:
    """discard_warmup drops the first (cold-start) value from latencies."""
    raw = [60.0, 5.0, 5.1, 4.9, 5.0, 5.2, 4.8, 5.1, 5.0, 4.9, 5.0]
    warmed = discard_warmup(raw)
    assert len(warmed) == 10
    assert 60.0 not in warmed
    # p50 should be near 5.0, not skewed by the 60.0 outlier
    p50 = compute_percentile(warmed, 50)
    assert p50 < 10.0


# ---------------------------------------------------------------------------
# Pyright strict
# ---------------------------------------------------------------------------


def test_pyright_strict_passes_on_benchmark_module() -> None:
    """pyright --strict reports zero errors on scripts/benchmark.py."""
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--pythonversion", "3.13", "scripts/benchmark.py"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).resolve().parents[3]),  # apps/backend/
    )
    assert result.returncode == 0, f"pyright errors:\n{result.stdout}\n{result.stderr}"

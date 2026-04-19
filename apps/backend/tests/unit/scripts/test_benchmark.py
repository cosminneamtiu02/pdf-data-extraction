"""Unit tests for ``scripts.benchmark`` — local latency benchmark script.

Covers the sixteen unit scenarios in PDFX-E007-F005: percentile computation
(happy path, single value, empty), report formatting (per-fixture table,
memory section, NFR comparison), fixture discovery (all present, one
missing, all missing), CLI parsing (--help, defaults, overrides, validation),
warm-up discard, and pyright strict pass.
"""

from __future__ import annotations

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
    through :class:`app.core.benchmark_settings.BenchmarkSettings`, so this
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

"""Local latency benchmark for the PDF extraction service (PDFX-E007-F005).

Sends sequential HTTP requests to a running instance of the extraction service
using a small fixture PDF corpus and prints p50 / p95 latency plus RSS
measurements for spot-checking against NFR-004 / NFR-005 / NFR-006 / NFR-008
targets.

This script is a pure HTTP client — it does NOT import ``app.main``,
FastAPI, Docling, LangExtract, PyMuPDF, or the Ollama HTTP client.  The
operator must start the service separately before running the benchmark.

Invocation
----------
- Via Taskfile:   ``task bench``
- Via module:     ``uv run python -m scripts.benchmark [--url URL] [--iterations N]``
- Programmatic:   ``from scripts.benchmark import run_benchmark``
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import resource  # Unix only
except ModuleNotFoundError:
    resource = None  # type: ignore[assignment]  # Windows fallback: RSS measurement unavailable

import httpx
import structlog
from pydantic import ValidationError

from scripts._benchmark_settings import BenchmarkSettings

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

EXPECTED_FIXTURES = (
    "native_invoice_10p.pdf",
    "scanned_invoice_10p.pdf",
    "table_heavy_5p.pdf",
)

OUTPUT_MODES = ("JSON_ONLY", "PDF_ONLY", "BOTH")

# NFR targets for comparison
NFR_TARGETS: dict[str, dict[str, float]] = {
    "native_invoice_10p": {"p50": 20.0, "p95": 45.0},  # NFR-004
    "scanned_invoice_10p": {"p50": 60.0, "p95": 120.0},  # NFR-005
}
NFR_ANNOTATION_OVERHEAD_S = 2.0  # NFR-006: annotation overhead <= 2 s
NFR_RSS_TARGET_MB = 1500.0  # NFR-008: idle RSS <= 1.5 GB


@dataclass(frozen=True, slots=True)
class BenchConfig:
    """Parsed CLI configuration for the benchmark."""

    url: str = "http://localhost:8000"
    iterations: int = 10
    fixtures_dir: Path = field(default_factory=lambda: Path("fixtures/bench"))
    skill_name: str = "invoice"
    skill_version: str = "1"
    warmup: int = 1
    timeout: float = 300.0
    service_pid: int | None = None


@dataclass(frozen=True, slots=True)
class FixtureInfo:
    """A discovered fixture PDF."""

    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class ModeBenchResult:
    """Latency results for one output mode."""

    latencies: list[float]


@dataclass(frozen=True, slots=True)
class FixtureBenchResult:
    """Latency results for one fixture across all output modes."""

    fixture_name: str
    modes: dict[str, ModeBenchResult]


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    """RSS measurements taken before and after the benchmark run.

    ``rss_before_mb`` / ``rss_after_mb`` are the benchmark *client*
    process's peak RSS (via ``resource.getrusage``).

    ``service_rss_before_mb`` / ``service_rss_after_mb`` are the
    *service* process's current RSS, measured via ``ps`` when
    ``--service-pid`` is provided.  ``None`` when the PID is not given
    or ``ps`` fails.
    """

    rss_before_mb: float
    rss_after_mb: float
    service_rss_before_mb: float | None = None
    service_rss_after_mb: float | None = None


@dataclass(frozen=True, slots=True)
class BenchResults:
    """Complete benchmark results."""

    fixtures: list[FixtureBenchResult]
    memory: MemorySnapshot


# ---------------------------------------------------------------------------
# Pure functions (unit-testable)
# ---------------------------------------------------------------------------


def compute_percentile(latencies: list[float], p: int) -> float:
    """Compute the *p*-th percentile of *latencies* via linear interpolation.

    Raises ``ValueError`` if *latencies* is empty.
    """
    if not latencies:
        msg = "Cannot compute percentile of an empty list"
        raise ValueError(msg)

    if len(latencies) == 1:
        return latencies[0]

    sorted_lat = sorted(latencies)
    n = len(sorted_lat)

    # Linear interpolation (same method as numpy's default / Excel PERCENTILE.INC)
    rank = (p / 100) * (n - 1)
    lower = int(rank)
    upper = lower + 1
    frac = rank - lower

    if upper >= n:
        return sorted_lat[-1]

    return sorted_lat[lower] + frac * (sorted_lat[upper] - sorted_lat[lower])


def discard_warmup(latencies: list[float], count: int = 1) -> list[float]:
    """Drop the first *count* values from *latencies* (warm-up discard)."""
    return latencies[count:]


def discover_fixtures(fixtures_dir: Path) -> list[FixtureInfo]:
    """Find the expected fixture PDFs in *fixtures_dir*.

    Raises ``FileNotFoundError`` naming each missing file.
    """
    missing: list[str] = []
    found: list[FixtureInfo] = []

    for name in EXPECTED_FIXTURES:
        path = fixtures_dir / name
        if path.is_file():
            found.append(FixtureInfo(name=Path(name).stem, path=path))
        else:
            missing.append(name)

    if missing:
        msg = f"Missing fixture PDF(s) in {fixtures_dir}: {', '.join(missing)}"
        raise FileNotFoundError(msg)

    return found


def _get_rss_mb() -> float:
    """Return peak (high-water-mark) RSS of this process in MB.

    Uses ``resource.getrusage`` on Unix.  Returns 0.0 on Windows where
    the ``resource`` module is unavailable.
    """
    if resource is None:
        return 0.0
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if platform.system() == "Darwin":
        # macOS: ru_maxrss is in bytes
        return usage.ru_maxrss / (1024 * 1024)
    # Linux: ru_maxrss is in kilobytes
    return usage.ru_maxrss / 1024


def _get_pid_rss_mb(pid: int) -> float | None:
    """Return current RSS of process *pid* in MB via ``ps``.

    Works on macOS and Linux without ``psutil``.  Returns ``None`` if the
    process does not exist, ``ps`` is not available, or parsing fails.
    """
    try:
        argv = ["ps", "-o", "rss=", "-p", str(pid)]
        result = subprocess.run(  # noqa: S603  # controlled argv, no shell injection
            argv,
            capture_output=True,
            text=True,
            check=True,
        )
        # ps reports RSS in kilobytes on both macOS and Linux
        return int(result.stdout.strip()) / 1024
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError, OSError):
        return None


def _pass_fail(value: float, target: float) -> str:
    """Return a pass/fail indicator."""
    return "\u2713 PASS" if value <= target else "\u2717 FAIL"


def _format_latency_table(lines: list[str], results: BenchResults) -> None:
    """Append the per-fixture latency table to *lines*."""
    lines.append("Latency Results")
    lines.append("-" * 78)
    lines.append(f"{'Fixture':<25} {'Mode':<12} {'Mean (s)':>10} {'P50 (s)':>10} {'P95 (s)':>10}")
    lines.append("-" * 78)

    for fixture in results.fixtures:
        for mode_name in OUTPUT_MODES:
            mode = fixture.modes.get(mode_name)
            if mode and mode.latencies:
                mean = sum(mode.latencies) / len(mode.latencies)
                p50 = compute_percentile(mode.latencies, 50)
                p95 = compute_percentile(mode.latencies, 95)
                lines.append(
                    f"{fixture.fixture_name:<25} {mode_name:<12} "
                    f"{mean:>10.2f} {p50:>10.2f} {p95:>10.2f}"
                )
            elif mode:
                lines.append(
                    f"{fixture.fixture_name:<25} {mode_name:<12} "
                    f"{'N/A':>10} {'N/A':>10} {'N/A':>10}"
                )


def _format_memory_section(lines: list[str], mem: MemorySnapshot) -> None:
    """Append the memory section(s) to *lines*."""
    lines.append("")
    lines.append("Memory (Peak RSS of benchmark client process)")
    lines.append("-" * 78)
    delta = mem.rss_after_mb - mem.rss_before_mb
    lines.append(f"  RSS before: {mem.rss_before_mb:>10.2f} MB")
    lines.append(f"  RSS after:  {mem.rss_after_mb:>10.2f} MB")
    lines.append(f"  RSS delta:  {delta:>10.2f} MB")

    if mem.service_rss_before_mb is not None and mem.service_rss_after_mb is not None:
        svc_delta = mem.service_rss_after_mb - mem.service_rss_before_mb
        lines.append("")
        lines.append("Service Memory (RSS via --service-pid)")
        lines.append("-" * 78)
        lines.append(f"  RSS before: {mem.service_rss_before_mb:>10.2f} MB")
        lines.append(f"  RSS after:  {mem.service_rss_after_mb:>10.2f} MB")
        lines.append(f"  RSS delta:  {svc_delta:>10.2f} MB")


def _format_nfr_comparison(lines: list[str], results: BenchResults) -> None:
    """Append the NFR target comparison to *lines*."""
    mem = results.memory
    lines.append("")
    lines.append("NFR Target Comparison")
    lines.append("-" * 78)
    lines.append(f"{'Metric':<35} {'Measured':>10} {'Target':>10} {'Result':>10}")
    lines.append("-" * 78)

    for fixture in results.fixtures:
        _format_fixture_nfr(lines, fixture)

    # NFR-008: use pre-run (idle) service RSS when --service-pid was provided
    if mem.service_rss_before_mb is not None:
        lines.append(
            f"  {'Service Idle RSS (NFR-008)':<35} "
            f"{mem.service_rss_before_mb:>7.1f} MB "
            f"{NFR_RSS_TARGET_MB:>7.1f} MB  "
            f"{_pass_fail(mem.service_rss_before_mb, NFR_RSS_TARGET_MB)}"
        )
    else:
        lines.append("  Service Idle RSS (NFR-008)         --service-pid not provided, skipped")


def _format_fixture_nfr(lines: list[str], fixture: FixtureBenchResult) -> None:
    """Append NFR rows for one fixture (latency targets + annotation overhead)."""
    targets = NFR_TARGETS.get(fixture.fixture_name)
    if targets:
        json_mode = fixture.modes.get("JSON_ONLY")
        if json_mode and json_mode.latencies:
            p50 = compute_percentile(json_mode.latencies, 50)
            p95 = compute_percentile(json_mode.latencies, 95)
            t_p50, t_p95 = targets["p50"], targets["p95"]
            label = fixture.fixture_name.replace("_", " ").title()
            lines.append(
                f"  {label + ' P50':<35} {p50:>10.2f} {t_p50:>7.1f}s  {_pass_fail(p50, t_p50)}"
            )
            lines.append(
                f"  {label + ' P95':<35} {p95:>10.2f} {t_p95:>7.1f}s  {_pass_fail(p95, t_p95)}"
            )

    # NFR-006: annotation overhead = PDF_ONLY_p50 - JSON_ONLY_p50
    json_mode = fixture.modes.get("JSON_ONLY")
    pdf_mode = fixture.modes.get("PDF_ONLY")
    if json_mode and json_mode.latencies and pdf_mode and pdf_mode.latencies:
        json_p50 = compute_percentile(json_mode.latencies, 50)
        pdf_p50 = compute_percentile(pdf_mode.latencies, 50)
        overhead = pdf_p50 - json_p50
        label = fixture.fixture_name.replace("_", " ").title()
        oh_label = f"{label} Annot. Overhead"
        oh_target = NFR_ANNOTATION_OVERHEAD_S
        lines.append(
            f"  {oh_label:<35} {overhead:>10.2f} "
            f"{oh_target:>7.1f}s  {_pass_fail(overhead, oh_target)}"
        )


def format_report(results: BenchResults) -> str:
    """Format benchmark results as a plain-text report."""
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 78)
    lines.append("  PDF Extraction Benchmark Report")
    lines.append("=" * 78)
    lines.append("")

    _format_latency_table(lines, results)
    _format_memory_section(lines, results.memory)
    _format_nfr_comparison(lines, results)

    lines.append("")
    lines.append("=" * 78)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def _send_request(  # noqa: PLR0913  # HTTP request builder — each param maps to a distinct form field
    client: httpx.Client,
    url: str,
    pdf_bytes: bytes,
    skill_name: str,
    skill_version: str,
    output_mode: str,
) -> float:
    """Send one extraction request and return wall-clock latency in seconds."""
    start = time.monotonic()
    response = client.post(
        f"{url}/api/v1/extract",
        data={
            "skill_name": skill_name,
            "skill_version": skill_version,
            "output_mode": output_mode,
        },
        files={"pdf": ("benchmark.pdf", pdf_bytes, "application/pdf")},
    )
    elapsed = time.monotonic() - start

    if response.status_code != 200:  # noqa: PLR2004  # HTTP 200 is a well-known constant
        _logger.warning(
            "bench_request_failed",
            status=response.status_code,
            output_mode=output_mode,
            elapsed=elapsed,
        )
        msg = f"Extraction request failed with status {response.status_code}: {response.text[:200]}"
        raise RuntimeError(msg)

    return elapsed


def run_benchmark(config: BenchConfig) -> BenchResults:
    """Run the full benchmark and return results.

    Raises ``FileNotFoundError`` if fixture PDFs are missing.
    Raises ``RuntimeError`` on non-200 HTTP responses from the service.
    Raises ``ConnectionError`` if the service is unreachable at the
    health-check step.  Raw ``httpx`` exceptions (``TimeoutException``,
    ``RequestError``) may propagate from individual extraction requests.
    """
    fixtures = discover_fixtures(config.fixtures_dir)
    rss_before = _get_rss_mb()
    svc_rss_before = _get_pid_rss_mb(config.service_pid) if config.service_pid else None

    fixture_results: list[FixtureBenchResult] = []

    total_requests = len(fixtures) * len(OUTPUT_MODES) * (config.iterations + config.warmup)
    _logger.info(
        "bench_start",
        url=config.url,
        iterations=config.iterations,
        warmup=config.warmup,
        fixtures=len(fixtures),
        total_requests=total_requests,
    )

    with httpx.Client(timeout=config.timeout) as client:
        # Verify the service is reachable
        try:
            health = client.get(f"{config.url}/health")
            health.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            msg = f"Cannot reach service at {config.url}: {exc}"
            raise ConnectionError(msg) from exc
        except httpx.HTTPStatusError as exc:
            msg = f"Service health check failed at {config.url}/health: {exc}"
            raise ConnectionError(msg) from exc

        for fixture in fixtures:
            pdf_bytes = fixture.path.read_bytes()
            mode_results: dict[str, ModeBenchResult] = {}

            for mode in OUTPUT_MODES:
                raw_latencies: list[float] = []

                for i in range(config.iterations + config.warmup):
                    elapsed = _send_request(
                        client,
                        config.url,
                        pdf_bytes,
                        config.skill_name,
                        config.skill_version,
                        mode,
                    )
                    raw_latencies.append(elapsed)
                    _logger.debug(
                        "bench_request",
                        fixture=fixture.name,
                        mode=mode,
                        iteration=i + 1,
                        elapsed_seconds=elapsed,
                    )

                latencies = discard_warmup(raw_latencies, config.warmup)
                mode_results[mode] = ModeBenchResult(latencies=latencies)

            fixture_results.append(
                FixtureBenchResult(fixture_name=fixture.name, modes=mode_results)
            )

    rss_after = _get_rss_mb()
    svc_rss_after = _get_pid_rss_mb(config.service_pid) if config.service_pid else None
    return BenchResults(
        fixtures=fixture_results,
        memory=MemorySnapshot(
            rss_before_mb=rss_before,
            rss_after_mb=rss_after,
            service_rss_before_mb=svc_rss_before,
            service_rss_after_mb=svc_rss_after,
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


# Argparse-name -> BenchmarkSettings-field mapping for the CLI flags that are
# backed by a ``BENCH_*`` env var. Every CLI knob the benchmark script exposes
# flows through :class:`BenchmarkSettings` so the env-CLI parity claim in the
# module docstring holds (issue #275).
_BENCH_FIELD_BY_ARG: dict[str, str] = {
    "url": "url",
    "iterations": "iterations",
    "fixtures_dir": "fixtures_dir",
    "skill_name": "skill_name",
    "skill_version": "skill_version",
    "service_pid": "service_pid",
    "warmup": "warmup",
    "timeout": "timeout",
}


def _format_bench_error_source(loc: str, cli_overrides: dict[str, Any]) -> str:
    """Return the operator-facing label for a ValidationError location.

    When the field was set via an explicit CLI flag, reference the flag name
    (``--iterations``). Otherwise reference the environment variable the user
    would have set (``BENCH_ITERATIONS``). This keeps the error message
    pointing at the input the operator actually supplied.
    """
    if loc in cli_overrides:
        return f"--{loc.replace('_', '-')}"
    return f"BENCH_{loc.upper()}"


def parse_args(argv: list[str]) -> BenchConfig:
    """Parse CLI arguments and return a ``BenchConfig``.

    Defaults come from :class:`scripts._benchmark_settings.BenchmarkSettings`,
    so ``BENCH_*`` environment variables flow through pydantic-settings
    rather than ``os.environ`` (CLAUDE.md forbidden pattern; issue #237).
    Explicit CLI flags still win over env vars.

    Implementation note: argv is parsed *first* with ``argparse.SUPPRESS`` as
    the default for every ``BENCH_*``-backed flag, so only flags the operator
    actually passed land on the argparse ``Namespace``. Those values are then
    forwarded to ``BenchmarkSettings(**cli_overrides)`` as init kwargs.
    pydantic-settings' documented source priority — init kwargs > env >
    defaults — means a bad ``BENCH_*`` value is only consulted when the CLI
    did not cover that field, so ``BENCH_ITERATIONS=banana --iterations 5``
    succeeds with ``iterations=5`` (PR #246 round-2 feedback).

    If a ``BENCH_*`` env value is syntactically invalid and the CLI does not
    override it, ``BenchmarkSettings(**cli_overrides)`` raises
    ``pydantic.ValidationError``; we translate that into an argparse-style
    operator error — concise stderr message and ``SystemExit(2)`` — so the
    script preserves the exit-code contract of the pre-refactor
    ``_safe_int_env`` branch instead of crashing with a traceback.
    """
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description=(
            "Local latency benchmark for the PDF extraction service. "
            "Sends sequential HTTP requests against a running service and "
            "prints p50/p95 latency plus RSS measurements."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables (read via BenchmarkSettings):\n"
            "  BENCH_URL            Override --url (default: http://localhost:8000)\n"
            "  BENCH_ITERATIONS     Override --iterations (default: 10)\n"
            "  BENCH_FIXTURES_DIR   Override --fixtures-dir (default: fixtures/bench)\n"
            "  BENCH_SKILL_NAME     Override --skill-name (default: invoice)\n"
            "  BENCH_SKILL_VERSION  Override --skill-version (default: 1)\n"
            "  BENCH_SERVICE_PID    Override --service-pid (default: none)\n"
            "  BENCH_WARMUP         Override --warmup (default: 1)\n"
            "  BENCH_TIMEOUT        Override --timeout (default: 300)\n"
        ),
    )
    # BenchmarkSettings-backed flags: SUPPRESS so only explicit CLI flags populate
    # the Namespace, then forwarded as init kwargs to override env/defaults.
    parser.add_argument(
        "--url",
        default=argparse.SUPPRESS,
        help="Base URL of the running extraction service (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=argparse.SUPPRESS,
        help="Number of timed iterations per fixture per mode (default: 10)",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Path to the benchmark fixture directory (default: fixtures/bench)",
    )
    parser.add_argument(
        "--skill-name",
        default=argparse.SUPPRESS,
        help="Skill name to use for extraction requests (default: invoice)",
    )
    parser.add_argument(
        "--skill-version",
        default=argparse.SUPPRESS,
        help="Skill version to use for extraction requests (default: 1)",
    )
    parser.add_argument(
        "--service-pid",
        type=int,
        default=argparse.SUPPRESS,
        help="PID of the running service process for RSS measurement (NFR-008)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=argparse.SUPPRESS,
        help="Number of warm-up requests to discard per batch (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=argparse.SUPPRESS,
        help="HTTP request timeout in seconds (default: 300)",
    )

    args = parser.parse_args(argv)

    # argparse returns per-flag values whose static type varies by the ``type=``
    # callable (``int`` / ``float`` / ``Path`` / ``str``); ``Any`` lets pyright
    # accept forwarding them as kwargs into ``BenchmarkSettings(**...)``, which
    # then runs pydantic validation on each field regardless.
    cli_overrides: dict[str, Any] = {
        _BENCH_FIELD_BY_ARG[arg_name]: value
        for arg_name, value in vars(args).items()
        if arg_name in _BENCH_FIELD_BY_ARG
    }

    try:
        settings = BenchmarkSettings(**cli_overrides)
    except ValidationError as exc:
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"]) or "<root>"
            source = _format_bench_error_source(loc, cli_overrides)
            sys.stderr.write(f"Error: {source}: {error['msg']}\n")
        raise SystemExit(2) from None  # operator-facing message, not a domain error

    # Normalize service_pid=0 -> None. PID 0 is never a valid target process
    # (POSIX reserves it for the current process group in signal semantics),
    # and ``ps -p 0`` produces no output. Matches the pre-refactor
    # ``_safe_int_env`` path which collapsed 0 via ``... or None``.
    service_pid = settings.service_pid or None

    return BenchConfig(
        url=settings.url.rstrip("/"),
        iterations=settings.iterations,
        fixtures_dir=settings.fixtures_dir,
        skill_name=settings.skill_name,
        skill_version=settings.skill_version,
        warmup=settings.warmup,
        timeout=settings.timeout,
        service_pid=service_pid,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns a process exit code."""
    try:
        config = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        # argparse calls sys.exit on --help or on error; propagate the code.
        return exc.code if isinstance(exc.code, int) else 1

    try:
        results = run_benchmark(config)
    except FileNotFoundError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    except (ConnectionError, httpx.ConnectError, httpx.TimeoutException) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    except RuntimeError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    sys.stdout.write(format_report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

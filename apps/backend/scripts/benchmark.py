"""Local latency benchmark for the PDF extraction service (PDFX-E007-F005).

Sends sequential HTTP requests to a running instance of the extraction service
using a small fixture PDF corpus and prints p50 / p95 latency plus RSS
measurements for spot-checking against NFR-004 / NFR-005 / NFR-008 targets.

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
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import resource  # Unix only
except ModuleNotFoundError:
    resource = None  # type: ignore[assignment]  # Windows fallback: RSS measurement unavailable

import httpx
import structlog

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
    """Peak (high-water-mark) RSS before and after the benchmark.

    These are the benchmark *client* process's peak RSS, not the service
    process's.  Operators should check the service process directly for
    NFR-008 (idle RSS <= 1.5 GB).
    """

    rss_before_mb: float
    rss_after_mb: float


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
    """Return peak (high-water-mark) RSS in MB.

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


def _pass_fail(value: float, target: float) -> str:
    """Return a pass/fail indicator."""
    return "\u2713 PASS" if value <= target else "\u2717 FAIL"


def format_report(results: BenchResults) -> str:
    """Format benchmark results as a plain-text report."""
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 78)
    lines.append("  PDF Extraction Benchmark Report")
    lines.append("=" * 78)
    lines.append("")

    # Per-fixture latency table
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

    # Memory section
    lines.append("")
    lines.append("Memory (Peak RSS of benchmark client process)")
    lines.append("-" * 78)
    mem = results.memory
    delta = mem.rss_after_mb - mem.rss_before_mb
    lines.append(f"  RSS before: {mem.rss_before_mb:>10.2f} MB")
    lines.append(f"  RSS after:  {mem.rss_after_mb:>10.2f} MB")
    lines.append(f"  RSS delta:  {delta:>10.2f} MB")

    # NFR comparison
    lines.append("")
    lines.append("NFR Target Comparison")
    lines.append("-" * 78)
    lines.append(f"{'Metric':<35} {'Measured':>10} {'Target':>10} {'Result':>10}")
    lines.append("-" * 78)

    for fixture in results.fixtures:
        targets = NFR_TARGETS.get(fixture.fixture_name)
        if targets:
            json_mode = fixture.modes.get("JSON_ONLY")
            if json_mode and json_mode.latencies:
                p50 = compute_percentile(json_mode.latencies, 50)
                p95 = compute_percentile(json_mode.latencies, 95)
                t_p50 = targets["p50"]
                t_p95 = targets["p95"]
                label = fixture.fixture_name.replace("_", " ").title()
                p50_label = f"{label} P50"
                p95_label = f"{label} P95"
                lines.append(
                    f"  {p50_label:<35} {p50:>10.2f} {t_p50:>7.1f}s  {_pass_fail(p50, t_p50)}"
                )
                lines.append(
                    f"  {p95_label:<35} {p95:>10.2f} {t_p95:>7.1f}s  {_pass_fail(p95, t_p95)}"
                )

    lines.append(f"  {'Note: RSS above is the benchmark client process.':<78}")
    lines.append(f"  {'Check the service process directly for NFR-008 (idle RSS <= 1.5 GB).':<78}")

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
    Raises ``RuntimeError`` on HTTP errors from the service.
    Raises ``httpx.ConnectError`` if the service is unreachable.
    """
    fixtures = discover_fixtures(config.fixtures_dir)
    rss_before = _get_rss_mb()

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
        except httpx.ConnectError as exc:
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
    return BenchResults(
        fixtures=fixture_results,
        memory=MemorySnapshot(rss_before_mb=rss_before, rss_after_mb=rss_after),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> BenchConfig:
    """Parse CLI arguments and return a ``BenchConfig``."""
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description=(
            "Local latency benchmark for the PDF extraction service. "
            "Sends sequential HTTP requests against a running service and "
            "prints p50/p95 latency plus RSS measurements."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  BENCH_URL           Override --url (default: http://localhost:8000)\n"
            "  BENCH_ITERATIONS    Override --iterations (default: 10)\n"
            "  BENCH_FIXTURES_DIR  Override --fixtures-dir (default: fixtures/bench)\n"
            "  BENCH_SKILL_NAME    Override --skill-name (default: invoice)\n"
            "  BENCH_SKILL_VERSION Override --skill-version (default: 1)\n"
        ),
    )
    parser.add_argument(
        "--url",
        default=_env_or("BENCH_URL", "http://localhost:8000"),
        help="Base URL of the running extraction service (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=int(_env_or("BENCH_ITERATIONS", "10")),
        help="Number of timed iterations per fixture per mode (default: 10)",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=Path(_env_or("BENCH_FIXTURES_DIR", "fixtures/bench")),
        help="Path to the benchmark fixture directory (default: fixtures/bench)",
    )
    parser.add_argument(
        "--skill-name",
        default=_env_or("BENCH_SKILL_NAME", "invoice"),
        help="Skill name to use for extraction requests (default: invoice)",
    )
    parser.add_argument(
        "--skill-version",
        default=_env_or("BENCH_SKILL_VERSION", "1"),
        help="Skill version to use for extraction requests (default: 1)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Number of warm-up requests to discard per batch (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="HTTP request timeout in seconds (default: 300)",
    )

    args = parser.parse_args(argv)

    if args.iterations < 1:
        parser.error("--iterations must be a positive integer")

    if args.warmup < 0:
        parser.error("--warmup must be a non-negative integer")

    return BenchConfig(
        url=args.url.rstrip("/"),
        iterations=args.iterations,
        fixtures_dir=args.fixtures_dir,
        skill_name=args.skill_name,
        skill_version=args.skill_version,
        warmup=args.warmup,
        timeout=args.timeout,
    )


def _env_or(key: str, default: str) -> str:
    """Read an env var via ``os.environ.get`` — benchmark scripts are
    operator-facing tools that read their own env vars, not pydantic-settings
    models.  The ``Settings`` class is for the FastAPI app process; this
    script runs outside the app process.
    """
    return os.environ.get(key, default)


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
    except (ConnectionError, httpx.ConnectError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    except RuntimeError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    sys.stdout.write(format_report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

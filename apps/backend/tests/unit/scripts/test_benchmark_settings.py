"""Tests for ``BenchmarkSettings`` — pydantic-settings model backing the
benchmark CLI (issue #237).

The benchmark script previously read ``BENCH_*`` variables via
``os.environ.get``, violating CLAUDE.md's categorical "Never use
``os.environ``" rule. These tests pin the replacement contract: a
``BenchmarkSettings`` model with the ``BENCH_`` env prefix that mirrors
the defaults the script used to hardcode, so ``task bench`` keeps working
without a shell-side change.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts._benchmark_settings import BenchmarkSettings


def test_benchmark_settings_defaults() -> None:
    """BenchmarkSettings constructs with the pre-refactor defaults.

    Passes ``_env_file=None`` to disable ``.env`` loading so a developer's
    local ``apps/backend/.env`` with ``BENCH_*`` overrides cannot make this
    test workstation-dependent — same pattern as ``test_config.py``.
    """
    s = BenchmarkSettings(_env_file=None)  # type: ignore[call-arg]
    assert s.url == "http://localhost:8000"
    assert s.iterations == 10
    assert s.fixtures_dir == Path("fixtures/bench")
    assert s.skill_name == "invoice"
    assert s.skill_version == "1"
    assert s.service_pid is None
    assert s.warmup == 1
    assert s.timeout == 300.0


def test_benchmark_settings_reads_env_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Each BENCH_* env var maps to the expected field."""
    fixtures_dir = tmp_path / "bench-fixtures"
    monkeypatch.setenv("BENCH_URL", "http://example.com:9000")
    monkeypatch.setenv("BENCH_ITERATIONS", "7")
    monkeypatch.setenv("BENCH_FIXTURES_DIR", str(fixtures_dir))
    monkeypatch.setenv("BENCH_SKILL_NAME", "receipt")
    monkeypatch.setenv("BENCH_SKILL_VERSION", "2")
    monkeypatch.setenv("BENCH_SERVICE_PID", "4242")
    monkeypatch.setenv("BENCH_WARMUP", "5")
    monkeypatch.setenv("BENCH_TIMEOUT", "60.5")

    s = BenchmarkSettings(_env_file=None)  # type: ignore[call-arg]

    assert s.url == "http://example.com:9000"
    assert s.iterations == 7
    assert s.fixtures_dir == fixtures_dir
    assert s.skill_name == "receipt"
    assert s.skill_version == "2"
    assert s.service_pid == 4242
    assert s.warmup == 5
    assert s.timeout == 60.5


def test_benchmark_settings_empty_service_pid_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset or empty BENCH_SERVICE_PID yields None (not 0)."""
    monkeypatch.delenv("BENCH_SERVICE_PID", raising=False)
    assert BenchmarkSettings(_env_file=None).service_pid is None  # type: ignore[call-arg]

    monkeypatch.setenv("BENCH_SERVICE_PID", "")
    assert BenchmarkSettings(_env_file=None).service_pid is None  # type: ignore[call-arg]


def test_benchmark_settings_rejects_invalid_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-integer BENCH_ITERATIONS value raises ValidationError."""
    monkeypatch.setenv("BENCH_ITERATIONS", "banana")
    with pytest.raises(ValidationError):
        BenchmarkSettings(_env_file=None)  # type: ignore[call-arg]


def test_benchmark_settings_rejects_invalid_service_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-integer BENCH_SERVICE_PID value raises ValidationError."""
    monkeypatch.setenv("BENCH_SERVICE_PID", "not-an-int")
    with pytest.raises(ValidationError):
        BenchmarkSettings(_env_file=None)  # type: ignore[call-arg]


def test_benchmark_settings_rejects_negative_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative BENCH_WARMUP value raises ValidationError (Field(ge=0))."""
    monkeypatch.setenv("BENCH_WARMUP", "-1")
    with pytest.raises(ValidationError):
        BenchmarkSettings(_env_file=None)  # type: ignore[call-arg]


def test_benchmark_settings_accepts_zero_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warmup=0 is valid (no warm-up iterations); guards Field(ge=0), not gt=0."""
    monkeypatch.setenv("BENCH_WARMUP", "0")
    assert BenchmarkSettings(_env_file=None).warmup == 0  # type: ignore[call-arg]


def test_benchmark_settings_rejects_zero_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero BENCH_TIMEOUT value raises ValidationError (Field(gt=0))."""
    monkeypatch.setenv("BENCH_TIMEOUT", "0")
    with pytest.raises(ValidationError):
        BenchmarkSettings(_env_file=None)  # type: ignore[call-arg]


def test_benchmark_settings_rejects_negative_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative BENCH_TIMEOUT value raises ValidationError (Field(gt=0))."""
    monkeypatch.setenv("BENCH_TIMEOUT", "-1.0")
    with pytest.raises(ValidationError):
        BenchmarkSettings(_env_file=None)  # type: ignore[call-arg]

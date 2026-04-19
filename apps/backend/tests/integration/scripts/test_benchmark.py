"""Integration tests for ``scripts.benchmark`` — PDFX-E007-F005.

These tests exercise the benchmark script's real HTTP client path against a
FastAPI app with ``ExtractionService`` stubbed via ``dependency_overrides``.
The app is served on a background thread so the benchmark makes real TCP
connections (not in-process ASGI transport).

Marked ``slow`` (module-level ``pytestmark``) because each test spins up a
real ``uvicorn`` server on a background thread with a 10-second startup
timeout. CLAUDE.md mandates the default ``task check`` loop run in <10s of
wall-clock; these tests therefore live under the ``slow`` marker and are
excluded from ``task check`` via the ``-m "not slow"`` filter (issue #283).
Run them explicitly with ``uv run pytest -m slow tests/integration/scripts/``.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import uvicorn
import yaml

from app.api.deps import get_extraction_service
from app.core.config import Settings
from app.exceptions import IntelligenceUnavailableError
from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extract_response import ExtractResponse
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
from app.features.extraction.schemas.field_status import FieldStatus
from app.features.extraction.service import ExtractionService
from app.main import create_app
from scripts.benchmark import main as bench_main

pytestmark = pytest.mark.slow

_FAKE_PDF_BYTES = b"%PDF-1.4 fake annotated content for benchmark tests"

FIXTURE_NAMES = [
    "native_invoice_10p.pdf",
    "scanned_invoice_10p.pdf",
    "table_heavy_5p.pdf",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_bench_skill(base: Path) -> None:
    """Write a minimal invoice skill matching the bench defaults."""
    body: dict[str, Any] = {
        "name": "invoice",
        "version": 1,
        "prompt": "Extract header fields from invoice.",
        "examples": [
            {
                "input": "Invoice #INV-001\nDate: 2025-01-15\nTotal: $1,234.56",
                "output": {
                    "invoice_number": "INV-001",
                    "date": "2025-01-15",
                    "total": "$1,234.56",
                    "vendor_name": "Unknown",
                },
            },
        ],
        "output_schema": {
            "type": "object",
            "properties": {
                "invoice_number": {"type": "string"},
                "date": {"type": "string"},
                "total": {"type": "string"},
                "vendor_name": {"type": "string"},
            },
            "required": ["invoice_number", "date", "total", "vendor_name"],
        },
    }
    target = base / "invoice"
    target.mkdir(parents=True, exist_ok=True)
    (target / "1.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _make_canned_result(
    *,
    annotated_pdf_bytes: bytes | None = None,
) -> ExtractionResult:
    field = ExtractedField(
        name="invoice_number",
        value="INV-001",
        status=FieldStatus.extracted,
        source="document",
        grounded=True,
        bbox_refs=[BoundingBoxRef(page=1, x0=10.0, y0=20.0, x1=100.0, y1=30.0)],
    )
    metadata = ExtractionMetadata(
        page_count=10,
        duration_ms=500,
        attempts_per_field={"invoice_number": 1},
    )
    response = ExtractResponse(
        skill_name="invoice",
        skill_version=1,
        fields={"invoice_number": field},
        metadata=metadata,
    )
    return ExtractionResult(response=response, annotated_pdf_bytes=annotated_pdf_bytes)


def _stub_service(
    result: ExtractionResult | None = None,
    *,
    side_effect: Exception | None = None,
) -> ExtractionService:
    svc = AsyncMock(spec=ExtractionService)
    if side_effect is not None:
        svc.extract.side_effect = side_effect
    else:
        svc.extract.return_value = result or _make_canned_result(
            annotated_pdf_bytes=_FAKE_PDF_BYTES,
        )
    return svc


def _write_fixture_pdfs(fixtures_dir: Path) -> None:
    """Write minimal valid PDF stubs for benchmark discovery."""
    for name in FIXTURE_NAMES:
        (fixtures_dir / name).write_bytes(b"%PDF-1.4 fixture stub content")


def _wait_for_server(server: uvicorn.Server, timeout_s: float = 10.0) -> None:
    """Block until *server.started* is True, or raise after *timeout_s*."""
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if server.started:
            return
        time.sleep(0.05)
    msg = f"Test server did not start within {timeout_s} seconds"
    raise TimeoutError(msg)


def _start_test_server(
    skills_dir: Path,
    stub: ExtractionService,
) -> tuple[threading.Thread, uvicorn.Server, int]:
    """Boot a real uvicorn server with the stubbed service.

    Reserves a port via SO_REUSEADDR to avoid TOCTOU races, then hands
    the socket to uvicorn.  Returns ``(thread, server, port)``.
    """
    settings = Settings(skills_dir=skills_dir, app_env="development")  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env
    app = create_app(settings)
    app.dependency_overrides[get_extraction_service] = lambda: stub

    # Reserve a port and let uvicorn inherit the socket
    reserved_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reserved_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    reserved_socket.bind(("127.0.0.1", 0))
    port = reserved_socket.getsockname()[1]

    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        fd=reserved_socket.fileno(),
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        _wait_for_server(server)
    except Exception:
        if reserved_socket is not None:
            reserved_socket.close()
        raise

    return thread, server, port


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


def test_benchmark_completes_against_stubbed_service(tmp_path: Path) -> None:
    """Bench completes exit 0 against a stubbed service, stdout has latency table."""
    skills_dir = tmp_path / "skills"
    _write_bench_skill(skills_dir)

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    _write_fixture_pdfs(fixtures_dir)

    stub = _stub_service()
    thread, server, port = _start_test_server(skills_dir, stub)

    try:
        import io
        import sys

        captured_out = io.StringIO()
        captured_err = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = captured_out, captured_err

        try:
            code = bench_main(
                [
                    "--url",
                    f"http://127.0.0.1:{port}",
                    "--iterations",
                    "1",
                    "--warmup",
                    "0",
                    "--fixtures-dir",
                    str(fixtures_dir),
                    "--timeout",
                    "30",
                ]
            )
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

        out = captured_out.getvalue()
        err = captured_err.getvalue()

        assert code == 0, f"bench exited {code}, stderr: {err}"
        assert "native_invoice_10p" in out
        assert "scanned_invoice_10p" in out
        assert "table_heavy_5p" in out
        assert "P50" in out or "p50" in out
        assert "P95" in out or "p95" in out
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_benchmark_unreachable_service_exits_nonzero(tmp_path: Path) -> None:
    """Bench exits non-zero within bounded time when service is unreachable."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    _write_fixture_pdfs(fixtures_dir)

    import io
    import sys
    import time

    captured_err = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured_err

    start = time.monotonic()
    try:
        code = bench_main(
            [
                "--url",
                "http://127.0.0.1:1",
                "--iterations",
                "1",
                "--warmup",
                "0",
                "--fixtures-dir",
                str(fixtures_dir),
                "--timeout",
                "5",
            ]
        )
    finally:
        sys.stderr = old_stderr

    elapsed = time.monotonic() - start
    err = captured_err.getvalue()

    assert code != 0
    assert elapsed < 30.0, f"benchmark hung for {elapsed:.1f}s"
    assert "127.0.0.1" in err or "connect" in err.lower() or "error" in err.lower()


def test_benchmark_missing_fixtures_exits_nonzero(tmp_path: Path) -> None:
    """Bench exits non-zero naming the missing fixture files."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    # Only write one fixture — two are missing
    (fixtures_dir / "native_invoice_10p.pdf").write_bytes(b"%PDF-1.4 stub")

    import io
    import sys

    captured_err = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured_err

    try:
        code = bench_main(
            [
                "--url",
                "http://127.0.0.1:1",
                "--iterations",
                "1",
                "--fixtures-dir",
                str(fixtures_dir),
            ]
        )
    finally:
        sys.stderr = old_stderr

    err = captured_err.getvalue()

    assert code != 0
    assert "scanned_invoice_10p.pdf" in err
    assert "table_heavy_5p.pdf" in err


def test_benchmark_extraction_errors_exit_nonzero(tmp_path: Path) -> None:
    """Bench exits non-zero when the service returns extraction errors."""
    skills_dir = tmp_path / "skills"
    _write_bench_skill(skills_dir)

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    _write_fixture_pdfs(fixtures_dir)

    stub = _stub_service(side_effect=IntelligenceUnavailableError())
    thread, server, port = _start_test_server(skills_dir, stub)

    try:
        import io
        import sys

        captured_err = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured_err

        try:
            code = bench_main(
                [
                    "--url",
                    f"http://127.0.0.1:{port}",
                    "--iterations",
                    "1",
                    "--warmup",
                    "0",
                    "--fixtures-dir",
                    str(fixtures_dir),
                    "--timeout",
                    "30",
                ]
            )
        finally:
            sys.stderr = old_stderr

        err = captured_err.getvalue()
        assert code != 0, f"Expected non-zero exit, got 0. stderr: {err}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)

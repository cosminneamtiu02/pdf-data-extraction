"""Integration tests for ``POST /api/v1/extract`` — PDFX-E006-F003.

All tests boot a real FastAPI app via ``create_app`` with a temporary skill
directory and override the ``ExtractionService`` dependency to avoid needing
real Docling / Ollama.  The ``httpx.AsyncClient`` sends requests through
``ASGITransport`` (in-process, no network).
"""

from __future__ import annotations

import email.parser
from pathlib import Path
from unittest.mock import AsyncMock

import yaml
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_extraction_service
from app.core.config import Settings
from app.exceptions import IntelligenceTimeoutError
from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extract_response import ExtractResponse
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
from app.features.extraction.schemas.field_status import FieldStatus
from app.features.extraction.service import ExtractionService
from app.main import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_PDF_BYTES = b"%PDF-1.4 fake annotated content"


def _write_valid_skill(base: Path) -> None:
    body = {
        "name": "invoice",
        "version": 1,
        "prompt": "Extract header fields.",
        "examples": [{"input": "INV-1", "output": {"number": "INV-1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
    }
    target = base / "invoice"
    target.mkdir(parents=True, exist_ok=True)
    (target / "1.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _settings(skills_dir: Path, **overrides) -> Settings:
    return Settings(skills_dir=skills_dir, app_env="development", **overrides)  # type: ignore[reportCallIssue]


def _make_canned_result(
    *,
    annotated_pdf_bytes: bytes | None = None,
) -> ExtractionResult:
    field = ExtractedField(
        name="number",
        value="INV-001",
        status=FieldStatus.extracted,
        source="document",
        grounded=True,
        bbox_refs=[BoundingBoxRef(page=1, x0=10.0, y0=20.0, x1=100.0, y1=30.0)],
    )
    metadata = ExtractionMetadata(
        page_count=1,
        duration_ms=500,
        attempts_per_field={"number": 1},
    )
    response = ExtractResponse(
        skill_name="invoice",
        skill_version=1,
        fields={"number": field},
        metadata=metadata,
    )
    return ExtractionResult(response=response, annotated_pdf_bytes=annotated_pdf_bytes)


def _stub_service(result: ExtractionResult | None = None, *, side_effect=None) -> ExtractionService:
    """Build a mock ``ExtractionService`` with a canned return or side effect."""
    svc = AsyncMock(spec=ExtractionService)
    if side_effect is not None:
        svc.extract.side_effect = side_effect
    else:
        svc.extract.return_value = result or _make_canned_result()
    return svc


def _build_app(tmp_path: Path, stub: ExtractionService, **settings_overrides):
    """Create a FastAPI app with the given stub service override."""
    _write_valid_skill(tmp_path)
    app = create_app(_settings(tmp_path, **settings_overrides))
    app.dependency_overrides[get_extraction_service] = lambda: stub
    return app


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


async def test_json_only_returns_200_json(tmp_path: Path) -> None:
    """POST with output_mode=JSON_ONLY returns 200 application/json."""
    stub = _stub_service(_make_canned_result(annotated_pdf_bytes=None))
    app = _build_app(tmp_path, stub)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "1",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4 small", "application/pdf")},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["skill_name"] == "invoice"
    assert body["skill_version"] == 1
    assert "number" in body["fields"]


async def test_pdf_only_returns_200_pdf(tmp_path: Path) -> None:
    """POST with output_mode=PDF_ONLY returns 200 application/pdf."""
    stub = _stub_service(_make_canned_result(annotated_pdf_bytes=_FAKE_PDF_BYTES))
    app = _build_app(tmp_path, stub)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "1",
                "output_mode": "PDF_ONLY",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4 small", "application/pdf")},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


async def test_both_returns_200_multipart_mixed(tmp_path: Path) -> None:
    """POST with output_mode=BOTH returns 200 multipart/mixed with two parts."""
    stub = _stub_service(_make_canned_result(annotated_pdf_bytes=_FAKE_PDF_BYTES))
    app = _build_app(tmp_path, stub)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "1",
                "output_mode": "BOTH",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4 small", "application/pdf")},
        )

    assert response.status_code == 200
    content_type = response.headers["content-type"]
    assert content_type.startswith('multipart/mixed; boundary="')

    # Parse the multipart response
    parser = email.parser.BytesParser()
    # Construct a full MIME message for parsing
    raw = b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + response.content
    msg = parser.parsebytes(raw)
    parts = list(msg.walk())
    # First is the multipart container, then the two parts
    actual_parts = [p for p in parts if not p.is_multipart()]
    assert len(actual_parts) == 2
    assert actual_parts[0].get_content_type() == "application/json"
    assert actual_parts[1].get_content_type() == "application/pdf"


async def test_oversized_pdf_returns_413(tmp_path: Path) -> None:
    """60 MB PDF against 50 MB limit returns 413 PDF_TOO_LARGE."""
    stub = _stub_service()
    max_bytes = 50 * 1024 * 1024
    app = _build_app(tmp_path, stub, max_pdf_bytes=max_bytes)
    transport = ASGITransport(app=app)

    # 60 MB of data — smaller chunk to avoid memory pressure in test
    oversized_data = b"x" * (60 * 1024 * 1024)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "1",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("big.pdf", oversized_data, "application/pdf")},
        )

    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "PDF_TOO_LARGE"
    assert body["error"]["params"]["max_bytes"] == max_bytes
    assert body["error"]["params"]["actual_bytes"] > max_bytes

    # Service must NOT have been called
    stub.extract.assert_not_called()


async def test_missing_skill_name_returns_422(tmp_path: Path) -> None:
    """Missing skill_name form field returns 422 (FastAPI native validation)."""
    stub = _stub_service()
    app = _build_app(tmp_path, stub)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_version": "1",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4", "application/pdf")},
        )

    assert response.status_code == 422


async def test_invalid_output_mode_returns_422(tmp_path: Path) -> None:
    """output_mode=XML returns 422."""
    stub = _stub_service()
    app = _build_app(tmp_path, stub)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "1",
                "output_mode": "XML",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4", "application/pdf")},
        )

    assert response.status_code == 422


async def test_intelligence_timeout_returns_504(tmp_path: Path) -> None:
    """Service raising IntelligenceTimeoutError returns 504 with error envelope."""
    stub = _stub_service(side_effect=IntelligenceTimeoutError(budget_seconds=180.0))
    app = _build_app(tmp_path, stub)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "1",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4", "application/pdf")},
        )

    assert response.status_code == 504
    body = response.json()
    assert body["error"]["code"] == "INTELLIGENCE_TIMEOUT"
    assert body["error"]["params"]["budget_seconds"] == 180.0


async def test_oversized_stream_aborts_before_service(tmp_path: Path) -> None:
    """51 MB streamed upload returns 413 and service is never called."""
    stub = _stub_service()
    max_bytes = 50 * 1024 * 1024
    app = _build_app(tmp_path, stub, max_pdf_bytes=max_bytes)
    transport = ASGITransport(app=app)

    oversized_data = b"x" * (max_bytes + 1024 * 1024)  # 51 MB

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "1",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("big.pdf", oversized_data, "application/pdf")},
        )

    assert response.status_code == 413
    stub.extract.assert_not_called()


async def test_skill_version_latest_passes_through(tmp_path: Path) -> None:
    """skill_version=latest is accepted and passed through to the service."""
    stub = _stub_service()
    app = _build_app(tmp_path, stub)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "latest",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4", "application/pdf")},
        )

    assert response.status_code == 200
    stub.extract.assert_called_once()
    call_args = stub.extract.call_args
    assert call_args[0][1] == "invoice"  # skill_name
    assert call_args[0][2] == "latest"  # skill_version


async def test_skill_version_zero_returns_422(tmp_path: Path) -> None:
    """skill_version=0 is rejected at the form-field boundary with 422."""
    stub = _stub_service()
    app = _build_app(tmp_path, stub)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "0",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4", "application/pdf")},
        )

    assert response.status_code == 422

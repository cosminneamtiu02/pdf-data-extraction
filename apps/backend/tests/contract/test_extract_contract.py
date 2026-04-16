"""Contract tests for ``POST /api/v1/extract`` — PDFX-E006-F003.

Validates the OpenAPI spec and error-response envelope shapes for the
extraction endpoint.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import yaml
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

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


def _make_canned_result() -> ExtractionResult:
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
    return ExtractionResult(response=response, annotated_pdf_bytes=None)


def test_openapi_contains_extract_endpoint(tmp_path: Path) -> None:
    """OpenAPI spec contains POST /api/v1/extract with correct form fields."""
    _write_valid_skill(tmp_path)
    app = create_app(_settings(tmp_path))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/openapi.json")
    assert response.status_code == 200

    spec = response.json()
    assert "/api/v1/extract" in spec["paths"]

    post_op = spec["paths"]["/api/v1/extract"]["post"]
    assert post_op is not None

    # Check that the operation expects multipart/form-data with the four fields
    request_body = post_op.get("requestBody", {})
    content = request_body.get("content", {})
    assert "multipart/form-data" in content

    schema = content["multipart/form-data"]["schema"]
    # FastAPI may use a $ref — resolve through components if needed
    if "$ref" in schema:
        ref_path = schema["$ref"]  # e.g. "#/components/schemas/Body_..."
        ref_name = ref_path.split("/")[-1]
        schema = spec["components"]["schemas"][ref_name]

    props = schema.get("properties", {})
    assert "pdf" in props
    assert "skill_name" in props
    assert "skill_version" in props
    assert "output_mode" in props

    # Check output_mode enum values — may be inline or a $ref to OutputMode
    output_mode_schema = props["output_mode"]
    if "$ref" in output_mode_schema:
        ref_name = output_mode_schema["$ref"].split("/")[-1]
        output_mode_schema = spec["components"]["schemas"][ref_name]
    if "enum" in output_mode_schema:
        assert set(output_mode_schema["enum"]) == {"JSON_ONLY", "PDF_ONLY", "BOTH"}


async def test_pdf_too_large_envelope_matches_contract(tmp_path: Path) -> None:
    """413 response for PDF_TOO_LARGE matches ErrorResponse schema shape."""
    stub = AsyncMock(spec=ExtractionService)
    _write_valid_skill(tmp_path)
    app = create_app(_settings(tmp_path, max_pdf_bytes=1024))
    app.dependency_overrides[get_extraction_service] = lambda: stub

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "1",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("big.pdf", b"x" * 2048, "application/pdf")},
        )

    assert response.status_code == 413
    body = response.json()
    error = body["error"]
    assert isinstance(error["code"], str)
    assert error["code"] == "PDF_TOO_LARGE"
    assert isinstance(error["params"]["max_bytes"], int)
    assert isinstance(error["params"]["actual_bytes"], int)
    assert "request_id" in error


async def test_intelligence_timeout_envelope_matches_contract(tmp_path: Path) -> None:
    """504 response for INTELLIGENCE_TIMEOUT matches ErrorResponse schema shape."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.side_effect = IntelligenceTimeoutError(budget_seconds=180.0)

    _write_valid_skill(tmp_path)
    app = create_app(_settings(tmp_path))
    app.dependency_overrides[get_extraction_service] = lambda: stub

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
    error = body["error"]
    assert isinstance(error["code"], str)
    assert error["code"] == "INTELLIGENCE_TIMEOUT"
    assert isinstance(error["params"]["budget_seconds"], (int, float))
    assert error["params"]["budget_seconds"] == 180.0
    assert "request_id" in error


async def test_extract_response_shape_conforms_to_schema(tmp_path: Path) -> None:
    """Verify the 200 response from a valid request matches ExtractResponse shape."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.return_value = _make_canned_result()

    _write_valid_skill(tmp_path)
    app = create_app(_settings(tmp_path))
    app.dependency_overrides[get_extraction_service] = lambda: stub

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

    assert response.status_code == 200
    body = response.json()
    # Verify the response has the ExtractResponse fields
    assert "skill_name" in body
    assert "skill_version" in body
    assert "fields" in body
    assert "metadata" in body
    # Verify metadata shape
    metadata = body["metadata"]
    assert "page_count" in metadata
    assert "duration_ms" in metadata
    assert "attempts_per_field" in metadata

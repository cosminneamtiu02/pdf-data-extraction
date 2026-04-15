"""Integration tests for PDFX-E004-F004 — intelligence-layer error contract.

Verifies that `IntelligenceUnavailableError` and `StructuredOutputFailedError`
raised from a request-handling path serialize through the DomainError
exception handler into the `ErrorResponse` envelope with the correct HTTP
status and machine-readable code.

We mount ad-hoc test routes rather than hitting `/api/v1/extract` — that
route does not yet exist (it lands in PDFX-E006). The scenario under test is
the wiring chain (DomainError → exception handler → JSON envelope), not any
specific endpoint. This mirrors `test_skill_error_contract.py`.
"""

from pathlib import Path

import yaml
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.exceptions import IntelligenceUnavailableError, StructuredOutputFailedError
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


def _settings_with_skills(skills_dir: Path) -> Settings:
    return Settings(skills_dir=skills_dir, app_env="development")  # type: ignore[reportCallIssue]


async def test_intelligence_unavailable_serializes_as_503_envelope(
    tmp_path: Path,
) -> None:
    _write_valid_skill(tmp_path)
    app = create_app(_settings_with_skills(tmp_path))

    async def _boom() -> None:
        raise IntelligenceUnavailableError

    app.add_api_route("/_test/intelligence-unavailable", _boom, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/intelligence-unavailable")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "INTELLIGENCE_UNAVAILABLE"
    assert body["error"]["params"] == {}
    assert body["error"]["details"] is None
    assert "request_id" in body["error"]


async def test_structured_output_failed_serializes_as_502_envelope(
    tmp_path: Path,
) -> None:
    _write_valid_skill(tmp_path)
    app = create_app(_settings_with_skills(tmp_path))

    async def _boom() -> None:
        raise StructuredOutputFailedError

    app.add_api_route("/_test/structured-output-failed", _boom, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/structured-output-failed")

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "STRUCTURED_OUTPUT_FAILED"
    assert body["error"]["params"] == {}
    assert body["error"]["details"] is None
    assert "request_id" in body["error"]


# Partial-success boundary: the acceptance criterion "2 of 3 fields succeed,
# 1 fails → HTTP 200 with per-field status, NOT 502" is deferred to
# PDFX-E006-F002, which is where the ExtractionService partial-vs-total
# failure decision actually lives. `StructuredOutputValidator` only raises
# `StructuredOutputFailedError` for a SINGLE field whose retries are
# exhausted; the per-field aggregation is outside the intelligence-layer
# error contract. The test for that boundary belongs to the PDFX-E006-F002
# integration suite. This comment-stub keeps the acceptance-criterion trace
# visible from this file.

"""Integration tests for ExtractionService (PDFX-E006-F002).

Uses ad-hoc test routes to exercise the service through the real FastAPI
exception handler chain. The extraction router (PDFX-E006-F003) does not
exist yet, so we mount throwaway routes that call ``ExtractionService``
directly, mirroring the pattern from ``test_intelligence_error_contract.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.features.extraction.coordinates.offset_index import OffsetIndex
from app.features.extraction.coordinates.offset_index_entry import OffsetIndexEntry
from app.features.extraction.extraction.raw_extraction import RawExtraction
from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.field_status import FieldStatus
from app.features.extraction.schemas.output_mode import OutputMode
from app.features.extraction.service import ExtractionService
from app.main import create_app

# ── Fixtures ────────────────────────────────────────────────────────────


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


def _settings_with_skills(skills_dir: Path, **extra: Any) -> Settings:
    return Settings(skills_dir=skills_dir, app_env="development", **extra)  # type: ignore[reportCallIssue]


def _parsed_doc() -> ParsedDocument:
    block = TextBlock(
        text="Invoice INV-001",
        page_number=1,
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=100.0, y1=20.0),
        block_id="p1_b0",
    )
    return ParsedDocument(blocks=(block,), page_count=1)


def _offset_index() -> OffsetIndex:
    return OffsetIndex(entries=[OffsetIndexEntry(start=0, end=15, block_id="p1_b0")])


def _raw_extractions() -> list[RawExtraction]:
    return [
        RawExtraction(
            field_name="number",
            value="INV-001",
            char_offset_start=8,
            char_offset_end=15,
            grounded=True,
            attempts=1,
        ),
    ]


def _all_failed_fields() -> list[ExtractedField]:
    return [
        ExtractedField(
            name="number",
            value=None,
            status=FieldStatus.failed,
            source="document",
            grounded=False,
            bbox_refs=[],
        ),
    ]


# ── Fake components for DI override ────────────────────────────────────


class _StubParser:
    async def parse(self, _pdf_bytes: bytes, _docling_config: Any) -> ParsedDocument:
        return _parsed_doc()


class _StubConcatenator:
    def concatenate(self, _document: ParsedDocument) -> tuple[str, OffsetIndex]:
        return "Invoice INV-001", _offset_index()


class _StubEngine:
    def __init__(self, *, sleep_seconds: float = 0.0) -> None:
        self._sleep_seconds = sleep_seconds

    async def extract(self, _text: str, _skill: Any, _provider: Any) -> list[RawExtraction]:
        if self._sleep_seconds > 0:
            await asyncio.sleep(self._sleep_seconds)
        return _raw_extractions()


class _StubResolver:
    def __init__(self, fields: list[ExtractedField] | None = None) -> None:
        self._fields = fields

    def resolve(
        self,
        _raw_extractions: list[RawExtraction],
        _offset_index: OffsetIndex,
        _parsed_document: ParsedDocument,
        declared_fields: list[str],
    ) -> list[ExtractedField]:
        if self._fields is not None:
            return self._fields
        return [
            ExtractedField(
                name=f,
                value="INV-001",
                status=FieldStatus.extracted,
                source="document",
                grounded=True,
                bbox_refs=[],
            )
            for f in declared_fields
        ]


class _StubAnnotator:
    async def annotate(self, _pdf_bytes: bytes, _fields: list[ExtractedField]) -> bytes:
        return b"%PDF-annotated"


class _StubProvider:
    pass


def _build_stub_service(
    app: Any,
    *,
    engine: _StubEngine | None = None,
    resolver: _StubResolver | None = None,
) -> ExtractionService:
    settings = app.state.settings
    return ExtractionService(
        skill_manifest=app.state.skill_manifest,
        document_parser=_StubParser(),
        text_concatenator=_StubConcatenator(),
        extraction_engine=engine or _StubEngine(),
        span_resolver=resolver or _StubResolver(),
        pdf_annotator=_StubAnnotator(),
        intelligence_provider=_StubProvider(),
        settings=settings,
    )


# ── Tests ───────────────────────────────────────────────────────────────


async def test_extraction_service_happy_path_through_fastapi_stack(
    tmp_path: Path,
) -> None:
    """Service wired via Depends returns a valid ExtractionResult."""
    _write_valid_skill(tmp_path)
    settings = _settings_with_skills(tmp_path)
    app = create_app(settings)
    service = _build_stub_service(app)

    async def _extract() -> dict[str, Any]:
        result = await service.extract(
            pdf_bytes=b"%PDF-test",
            skill_name="invoice",
            skill_version="1",
            output_mode=OutputMode.JSON_ONLY,
        )
        return result.response.model_dump()

    app.add_api_route("/_test/extract", _extract, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/extract")

    assert response.status_code == 200
    body = response.json()
    assert body["skill_name"] == "invoice"
    assert body["skill_version"] == 1
    assert "number" in body["fields"]
    assert body["metadata"]["page_count"] == 1


async def test_extraction_service_timeout_serializes_as_504_envelope(
    tmp_path: Path,
) -> None:
    """Timeout → IntelligenceTimeoutError → 504 via exception handler."""
    _write_valid_skill(tmp_path)
    settings = _settings_with_skills(tmp_path, extraction_timeout_seconds=0.1)
    app = create_app(settings)
    service = _build_stub_service(app, engine=_StubEngine(sleep_seconds=0.2))

    async def _extract() -> None:
        await service.extract(
            pdf_bytes=b"%PDF-test",
            skill_name="invoice",
            skill_version="1",
            output_mode=OutputMode.JSON_ONLY,
        )

    app.add_api_route("/_test/extract-timeout", _extract, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/extract-timeout")

    assert response.status_code == 504
    body = response.json()
    assert body["error"]["code"] == "INTELLIGENCE_TIMEOUT"
    assert body["error"]["params"] == {"budget_seconds": 0.1}
    assert "request_id" in body["error"]


async def test_extraction_service_all_failed_serializes_as_502_envelope(
    tmp_path: Path,
) -> None:
    """All fields failed → StructuredOutputFailedError → 502."""
    _write_valid_skill(tmp_path)
    settings = _settings_with_skills(tmp_path)
    app = create_app(settings)
    service = _build_stub_service(
        app,
        resolver=_StubResolver(fields=_all_failed_fields()),
    )

    async def _extract() -> None:
        await service.extract(
            pdf_bytes=b"%PDF-test",
            skill_name="invoice",
            skill_version="1",
            output_mode=OutputMode.JSON_ONLY,
        )

    app.add_api_route("/_test/extract-all-failed", _extract, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/extract-all-failed")

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "STRUCTURED_OUTPUT_FAILED"


async def test_extraction_service_skill_not_found_serializes_as_404_envelope(
    tmp_path: Path,
) -> None:
    """SkillNotFoundError → 404 via exception handler."""
    _write_valid_skill(tmp_path)
    settings = _settings_with_skills(tmp_path)
    app = create_app(settings)
    service = _build_stub_service(app)

    async def _extract() -> None:
        await service.extract(
            pdf_bytes=b"%PDF-test",
            skill_name="nonexistent",
            skill_version="1",
            output_mode=OutputMode.JSON_ONLY,
        )

    app.add_api_route("/_test/extract-not-found", _extract, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/extract-not-found")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "SKILL_NOT_FOUND"
    assert body["error"]["params"]["name"] == "nonexistent"
    assert body["error"]["params"]["version"] == "1"

"""Shared contract-test helpers.

Both `test_extract_contract.py` and `test_schemathesis.py` need the same
three fixtures to drive `POST /api/v1/extract`: a valid skill YAML on
disk, a `Settings` instance that points at it (with `app_env=development`
so `/openapi.json` is exposed), and a canned `ExtractionResult` for the
200 happy path. Keeping one definition here instead of two copies means
a contract-envelope change lands in one place; the two test files drift
less as the spec evolves.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.core.config import Settings
from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extract_response import ExtractResponse
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
from app.features.extraction.schemas.field_status import FieldStatus


def write_valid_skill(base: Path) -> None:
    """Write a minimally valid `invoice@1` skill YAML under ``base``."""
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


def settings(skills_dir: Path, **overrides: object) -> Settings:
    """Return a `Settings` pinned to ``skills_dir`` with `app_env=development`.

    Explicit `app_env="development"` keeps `/openapi.json` served even
    when the ambient environment has `APP_ENV=production` set (which
    `create_app` uses to disable the OpenAPI route in prod).
    """
    return Settings(skills_dir=skills_dir, app_env="development", **overrides)  # type: ignore[reportCallIssue]


def make_canned_result() -> ExtractionResult:
    """Return a canned `ExtractionResult` for the 200 happy-path contract test."""
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

"""Pin the "every declared field always present" API-stability invariant.

Issue #351: the invariant is called load-bearing in CLAUDE.md:

    Never return a response shape that omits a field declared by the skill's
    ``output_schema``. The "every declared field always present" invariant is
    load-bearing for API stability.

Prior to this module the invariant was exercised only by
``test_live_ollama_e2e.py::test_extract_endpoint_end_to_end_against_live_ollama``,
which is gated by ``@pytest.mark.slow`` + skipif-Docling + skipif-fixture +
skipif-Ollama-reachable and therefore always SKIPPED under the default
``task check`` run — zero coverage.

This module parametrizes three skills with different declared-field counts
(1, 3, 5 fields) against the real ``/api/v1/extract`` endpoint with the
pipeline components stubbed so no Ollama / Docling / Gemma is needed. The
real ``SpanResolver`` runs — that is where ``_synthesize_missing`` fills
placeholders for declared fields the engine did not produce. The stub
``ExtractionEngine`` deliberately returns ONLY ONE raw extraction regardless
of how many fields the skill declared, forcing the resolver's placeholder
path to run for every remaining declared field. If the fill code regresses
(e.g. the resolver stops iterating ``declared_fields`` or a future refactor
drops the ``_synthesize_missing`` branch), the response will omit some of
the declared fields and this test will fail with the exact missing names
in the assertion diff.

Why not just re-use the slow E2E test? Because slow tests are excluded
from ``task check`` and running them requires a live Ollama plus a pulled
Gemma model. The invariant needs coverage in the fast default lane.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_document_parser
from app.core.config import Settings
from app.features.extraction.coordinates.offset_index import OffsetIndex
from app.features.extraction.coordinates.offset_index_entry import OffsetIndexEntry
from app.features.extraction.deps import (
    get_extraction_engine,
    get_pdf_annotator,
    get_text_concatenator,
)
from app.features.extraction.extraction.raw_extraction import RawExtraction
from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock
from app.main import create_app


def _write_skill(base: Path, name: str, fields: tuple[str, ...]) -> None:
    """Write a skill YAML file declaring ``fields`` in ``base/<name>/1.yaml``."""
    body: dict[str, Any] = {
        "name": name,
        "version": 1,
        "prompt": f"Extract {', '.join(fields)} from the document.",
        "examples": [
            {
                "input": "example",
                "output": {field: f"demo-{field}" for field in fields},
            },
        ],
        "output_schema": {
            "type": "object",
            "properties": {field: {"type": "string"} for field in fields},
            "required": list(fields),
        },
    }
    target = base / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "1.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _parsed_document_with_value(first_field_value: str) -> ParsedDocument:
    """Return a single-block document containing ``first_field_value`` at offset 0."""
    block = TextBlock(
        text=first_field_value,
        page_number=1,
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=100.0, y1=20.0),
        block_id="p1_b0",
    )
    return ParsedDocument(blocks=(block,), page_count=1)


class _StubParser:
    """Return a canned ``ParsedDocument`` without touching Docling / the PDF bytes."""

    def __init__(self, document: ParsedDocument) -> None:
        self._document = document

    async def parse(self, _pdf_bytes: bytes, _docling_config: Any) -> ParsedDocument:
        return self._document


class _StubConcatenator:
    """Return a deterministic concatenated text + offset index for the one block."""

    def __init__(self, document: ParsedDocument) -> None:
        self._document = document

    def concatenate(self, _document: ParsedDocument) -> tuple[str, OffsetIndex]:
        block = self._document.blocks[0]
        index = OffsetIndex(
            entries=[
                OffsetIndexEntry(start=0, end=len(block.text), block_id=block.block_id),
            ],
        )
        return block.text, index


class _PartialEngine:
    """Return only ONE real extraction regardless of how many fields are declared.

    The real ``ExtractionEngine`` also synthesizes placeholders for declared
    fields LangExtract did not return, but that path is covered by its own
    unit + integration tests. Here we want to expose the ``SpanResolver``'s
    placeholder path: return a raw list with ONLY the first declared field
    populated, then let ``SpanResolver`` surface placeholders for the rest.

    At least one field must be grounded+extracted or ``ExtractionService``
    would raise ``StructuredOutputFailedError`` (502) before the response is
    assembled, short-circuiting the test.
    """

    def __init__(self, first_field_name: str, first_field_value: str) -> None:
        self._first_field_name = first_field_name
        self._first_field_value = first_field_value

    async def extract(
        self,
        _concatenated_text: str,
        _skill: Any,
        _provider: Any,
    ) -> list[RawExtraction]:
        return [
            RawExtraction(
                field_name=self._first_field_name,
                value=self._first_field_value,
                char_offset_start=0,
                char_offset_end=len(self._first_field_value),
                grounded=True,
                attempts=1,
            ),
        ]


class _StubAnnotator:
    """Return placeholder annotated PDF bytes — the route never requests them
    under ``OutputMode.JSON_ONLY`` but the service still constructs the
    annotator dependency.
    """

    async def annotate(self, pdf_bytes: bytes, _fields: list[Any]) -> bytes:
        return pdf_bytes


# (skill_name, declared_fields) — three skills with distinct field counts (1, 3, 5)
# so the test fails loudly whichever bucket the regression hits (single-field
# path, small-set path, or wider-set path).
_SKILL_CASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("one_field_skill", ("number",)),
    ("three_field_skill", ("number", "date", "vendor")),
    ("five_field_skill", ("invoice_id", "date", "vendor", "total", "currency")),
)


@pytest.mark.parametrize(
    ("skill_name", "declared_fields"),
    _SKILL_CASES,
    ids=[case[0] for case in _SKILL_CASES],
)
async def test_response_contains_every_declared_field_regardless_of_llm_output(
    tmp_path: Path,
    skill_name: str,
    declared_fields: tuple[str, ...],
) -> None:
    """``response["fields"]`` always contains every name declared by the skill.

    The stub ``ExtractionEngine`` returns ONE raw extraction (only the first
    declared field) but the real ``SpanResolver`` must synthesize
    ``FieldStatus.failed`` placeholders for every remaining declared field so
    the response-body keys match the declared set exactly. A regression that
    skips the placeholder fill (or silently drops some declared field) will
    fail the set-equality assertion with a diff listing the missing names.
    """
    # Write all three skills so the SkillManifest has them available; the
    # test parameter picks which one to request.
    for name, fields in _SKILL_CASES:
        _write_skill(tmp_path, name, fields)

    first_field_value = f"demo-{declared_fields[0]}"
    parsed = _parsed_document_with_value(first_field_value)

    settings = Settings(skills_dir=tmp_path, app_env="development")  # type: ignore[reportCallIssue]  # pydantic-settings loads from env
    app = create_app(settings)

    app.dependency_overrides[get_document_parser] = lambda: _StubParser(parsed)
    app.dependency_overrides[get_text_concatenator] = lambda: _StubConcatenator(parsed)
    app.dependency_overrides[get_extraction_engine] = lambda: _PartialEngine(
        first_field_name=declared_fields[0],
        first_field_value=first_field_value,
    )
    stub_annotator = _StubAnnotator()
    app.dependency_overrides[get_pdf_annotator] = lambda: stub_annotator

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": skill_name,
                "skill_version": "1",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4 stub", "application/pdf")},
        )

    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {response.text[:500]}"
    )
    body = response.json()
    assert body["skill_name"] == skill_name
    assert body["skill_version"] == 1

    # The load-bearing assertion: set-equality between declared fields and the
    # response-body keys. A regression that omits any declared field fails
    # here with the missing names in the diff.
    assert set(body["fields"].keys()) == set(declared_fields), (
        f"response fields {set(body['fields'].keys())} do not match "
        f"declared fields {set(declared_fields)} for skill {skill_name!r}"
    )

    # Metadata's per-field attempts counter must also cover every declared
    # field — otherwise operators reading the attempts histogram could draw
    # false conclusions about fields that silently vanished from the fields
    # mapping. Keep both invariants pinned together.
    assert set(body["metadata"]["attempts_per_field"].keys()) == set(declared_fields)

    # The field that the engine produced must be ``extracted``; every other
    # declared field must be a ``failed`` placeholder (value=None). This is
    # what pins the behavior to "synthesize placeholders" rather than "accept
    # any legal response shape": a regression that simply DROPPED the
    # missing fields (leaving the response keys a strict subset of declared)
    # would fail the set-equality above, but a regression that faked
    # successful extractions for missing fields would fail HERE instead.
    extracted_field = body["fields"][declared_fields[0]]
    assert extracted_field["status"] == "extracted"
    assert extracted_field["value"] == first_field_value
    for field_name in declared_fields[1:]:
        placeholder = body["fields"][field_name]
        assert placeholder["status"] == "failed", (
            f"field {field_name!r} for skill {skill_name!r} should be a failed "
            f"placeholder but got status={placeholder['status']!r}"
        )
        assert placeholder["value"] is None
        assert placeholder["grounded"] is False
        assert placeholder["bbox_refs"] == []

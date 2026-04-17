"""Per-component ``Depends()`` override integration test (issue #111).

The feature-level ``deps.py`` documents per-component factories
(``get_text_concatenator``, ``get_extraction_engine``, ``get_span_resolver``,
``get_pdf_annotator``) so integration tests can override a single pipeline
component via ``app.dependency_overrides[get_text_concatenator] = ...``
without replacing the entire ``ExtractionService``.

Before this fix, ``get_extraction_service`` in ``app/api/deps.py`` constructed
each component directly (e.g. ``TextConcatenator()``), bypassing the
overridable factories — overrides documented in ``deps.py`` had no effect.
This test pins the behavior by overriding one per-component factory and
asserting the stub actually ran inside the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.features.extraction.coordinates.offset_index import OffsetIndex
from app.features.extraction.coordinates.offset_index_entry import OffsetIndexEntry
from app.features.extraction.coordinates.text_concatenator import TextConcatenator
from app.features.extraction.deps import get_text_concatenator
from app.features.extraction.extraction.extraction_engine import ExtractionEngine
from app.features.extraction.extraction.raw_extraction import RawExtraction
from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock
from app.features.extraction.schemas.output_mode import OutputMode
from app.main import create_app

if TYPE_CHECKING:
    from app.features.extraction.intelligence.intelligence_provider import (
        IntelligenceProvider,
    )
    from app.features.extraction.parsing.document_parser import DocumentParser
    from app.features.extraction.service import ExtractionService

# A sentinel value the stub concatenator emits so the test can assert the
# overridden factory actually ran inside the pipeline.
_SENTINEL_TEXT = "SENTINEL_CONCATENATOR_OUTPUT_issue_111"


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


class _StubParser:
    """Parser stub returning a one-block document (no real Docling)."""

    async def parse(self, _pdf_bytes: bytes, _docling_config: Any) -> ParsedDocument:
        block = TextBlock(
            text="Invoice INV-001",
            page_number=1,
            bbox=BoundingBox(x0=0.0, y0=0.0, x1=100.0, y1=20.0),
            block_id="p1_b0",
        )
        return ParsedDocument(blocks=(block,), page_count=1)


class _StubIntelligenceProvider:
    """Placeholder — the real extraction engine is replaced by the stub below."""


class _StubExtractionEngine:
    """Engine stub that records the text it was asked to extract from.

    The sentinel from the overridden concatenator flows into ``extract`` as
    ``concatenated_text``; the engine stores it so the test can assert the
    stub concatenator was the one that produced it.
    """

    def __init__(self) -> None:
        self.observed_text: str | None = None

    async def extract(
        self,
        text: str,
        _skill: Any,
        _provider: IntelligenceProvider,
    ) -> list[RawExtraction]:
        self.observed_text = text
        return [
            RawExtraction(
                field_name="number",
                value="INV-001",
                char_offset_start=0,
                char_offset_end=len(text),
                grounded=True,
                attempts=1,
            ),
        ]


class _SentinelConcatenator:
    """Concatenator stub that emits a distinguishable sentinel string.

    If ``get_extraction_service`` still instantiates ``TextConcatenator()``
    directly, the sentinel never reaches the extraction engine and the test
    fails — which is the whole point of this regression guard.
    """

    def concatenate(self, _document: ParsedDocument) -> tuple[str, OffsetIndex]:
        return _SENTINEL_TEXT, OffsetIndex(
            entries=[OffsetIndexEntry(start=0, end=len(_SENTINEL_TEXT), block_id="p1_b0")],
        )


async def test_overriding_get_text_concatenator_affects_real_extraction_service(
    tmp_path: Path,
) -> None:
    """Overriding ``get_text_concatenator`` must affect the wired service.

    Boots the real app, installs a ``dependency_overrides`` entry on
    ``get_text_concatenator`` pointing at ``_SentinelConcatenator``, plus
    narrow overrides on the I/O-bound factories (parser, intelligence
    provider) so the pipeline does not touch Docling or Ollama.  Then
    asserts the sentinel string emitted by the stub concatenator reached
    the extraction engine — which can only happen if
    ``get_extraction_service`` resolved the text concatenator through the
    overridden factory rather than constructing ``TextConcatenator()``
    inline.
    """
    from app.api.deps import (
        get_document_parser,
        get_extraction_service,
        get_intelligence_provider,
    )
    from app.features.extraction.deps import get_extraction_engine

    _write_valid_skill(tmp_path)
    settings = Settings(skills_dir=tmp_path, app_env="development")  # type: ignore[reportCallIssue]
    app = create_app(settings)

    stub_engine = _StubExtractionEngine()
    stub_parser: DocumentParser = _StubParser()
    stub_provider = _StubIntelligenceProvider()
    sentinel_concatenator = _SentinelConcatenator()

    # Override the per-component factories. The text concatenator is the
    # *subject* of this test; the other overrides exist only so the
    # pipeline does not need real Docling or Ollama.
    app.dependency_overrides[get_text_concatenator] = lambda: sentinel_concatenator
    app.dependency_overrides[get_document_parser] = lambda: stub_parser
    app.dependency_overrides[get_extraction_engine] = lambda: stub_engine
    app.dependency_overrides[get_intelligence_provider] = lambda: stub_provider

    # A thin route that delegates to the DI-resolved ExtractionService, so
    # the resolution goes through the full `Depends()` graph including the
    # overridden concatenator factory.
    from fastapi import Depends

    async def _extract(
        service: ExtractionService = Depends(get_extraction_service),  # noqa: B008 — FastAPI DI
    ) -> dict[str, Any]:
        result = await service.extract(
            b"%PDF-test",
            "invoice",
            "1",
            OutputMode.JSON_ONLY,
        )
        return result.response.model_dump()

    app.add_api_route("/_test/extract-deps", _extract, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/extract-deps")

    assert response.status_code == 200, response.text
    # The sentinel must have reached the extraction engine. If the service
    # was built with a direct `TextConcatenator()` call, observed_text would
    # be the real concatenator's output ("Invoice INV-001"), not the sentinel.
    assert stub_engine.observed_text == _SENTINEL_TEXT, (
        "The overridden get_text_concatenator factory was bypassed: "
        "get_extraction_service constructed TextConcatenator() directly "
        "instead of resolving it via Depends(get_text_concatenator). "
        f"Observed text: {stub_engine.observed_text!r}"
    )


async def test_default_get_text_concatenator_still_wires_real_component(
    tmp_path: Path,
) -> None:
    """Without an override, the real ``TextConcatenator`` is still wired in.

    Ensures the refactor does not regress the production default — the
    service must still construct and use a real ``TextConcatenator`` when
    no ``dependency_overrides`` entry exists.
    """
    from app.api.deps import (
        get_document_parser,
        get_extraction_service,
        get_intelligence_provider,
    )
    from app.features.extraction.deps import get_extraction_engine

    _write_valid_skill(tmp_path)
    settings = Settings(skills_dir=tmp_path, app_env="development")  # type: ignore[reportCallIssue]
    app = create_app(settings)

    stub_engine = _StubExtractionEngine()
    stub_parser: DocumentParser = _StubParser()
    stub_provider = _StubIntelligenceProvider()

    app.dependency_overrides[get_document_parser] = lambda: stub_parser
    app.dependency_overrides[get_extraction_engine] = lambda: stub_engine
    app.dependency_overrides[get_intelligence_provider] = lambda: stub_provider

    from fastapi import Depends

    async def _extract(
        service: ExtractionService = Depends(get_extraction_service),  # noqa: B008 — FastAPI DI
    ) -> dict[str, Any]:
        await service.extract(
            b"%PDF-test",
            "invoice",
            "1",
            OutputMode.JSON_ONLY,
        )
        # Reflect the concrete concatenator class back to the test for assertion.
        return {"concatenator_class": type(service._text_concatenator).__name__}  # noqa: SLF001

    app.add_api_route("/_test/extract-default", _extract, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/extract-default")

    assert response.status_code == 200, response.text
    assert response.json()["concatenator_class"] == TextConcatenator.__name__
    # And the real concatenator actually ran — its output is NOT the sentinel.
    assert stub_engine.observed_text != _SENTINEL_TEXT
    assert stub_engine.observed_text == "Invoice INV-001"


async def test_ensures_engine_factory_is_also_overrideable(
    tmp_path: Path,
) -> None:
    """Smoke check for ``get_extraction_engine`` override, for completeness.

    Not strictly required to close issue #111, but guards against a
    regression where only one of the four per-component factories is
    wired.
    """
    from app.api.deps import (
        get_document_parser,
        get_extraction_service,
        get_intelligence_provider,
    )
    from app.features.extraction.deps import get_extraction_engine

    _write_valid_skill(tmp_path)
    settings = Settings(skills_dir=tmp_path, app_env="development")  # type: ignore[reportCallIssue]
    app = create_app(settings)

    stub_engine = _StubExtractionEngine()
    stub_parser: DocumentParser = _StubParser()
    stub_provider = _StubIntelligenceProvider()

    app.dependency_overrides[get_document_parser] = lambda: stub_parser
    app.dependency_overrides[get_extraction_engine] = lambda: stub_engine
    app.dependency_overrides[get_intelligence_provider] = lambda: stub_provider

    from fastapi import Depends

    async def _extract(
        service: ExtractionService = Depends(get_extraction_service),  # noqa: B008 — FastAPI DI
    ) -> dict[str, Any]:
        await service.extract(
            b"%PDF-test",
            "invoice",
            "1",
            OutputMode.JSON_ONLY,
        )
        return {
            "engine_class": type(service._extraction_engine).__name__,  # noqa: SLF001
            "is_stub": isinstance(service._extraction_engine, _StubExtractionEngine),  # noqa: SLF001
        }

    app.add_api_route("/_test/extract-engine", _extract, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/extract-engine")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_stub"] is True, (
        "get_extraction_engine override was bypassed: service built a real "
        f"{ExtractionEngine.__name__} instead of the stub "
        f"(class in service: {body['engine_class']})"
    )

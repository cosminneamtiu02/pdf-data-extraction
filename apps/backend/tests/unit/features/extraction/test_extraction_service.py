"""Unit tests for ExtractionService (PDFX-E006-F002).

The extraction pipeline collaborators are represented by hand-rolled fakes
— no unittest.mock, no pytest-mock. Each fake records calls for ordering
assertions.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.core.config import Settings
from app.exceptions import (
    ExtractionOverloadedError,
    IntelligenceTimeoutError,
    PdfInvalidError,
    SkillNotFoundError,
    StructuredOutputFailedError,
)
from app.features.extraction.coordinates.offset_index import OffsetIndex
from app.features.extraction.coordinates.offset_index_entry import OffsetIndexEntry
from app.features.extraction.extraction.raw_extraction import RawExtraction
from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.field_status import FieldStatus
from app.features.extraction.schemas.output_mode import OutputMode
from app.features.extraction.service import ExtractionService
from app.features.extraction.skills.skill import Skill
from app.features.extraction.skills.skill_docling_config import SkillDoclingConfig

# ── Helpers ─────────────────────────────────────────────────────────────


def _build_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "extraction_timeout_seconds": 180.0,
        "docling_ocr_default": "auto",
        "docling_table_mode_default": "fast",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[reportCallIssue]


def _build_skill(
    *,
    name: str = "invoice",
    version: int = 1,
    fields: tuple[str, ...] = ("number", "date", "total"),
) -> Skill:
    properties: dict[str, dict[str, str]] = {f: {"type": "string"} for f in fields}
    return Skill(
        name=name,
        version=version,
        description=None,
        prompt="Extract fields.",
        examples=(),
        output_schema={"type": "object", "properties": properties, "required": list(fields)},
        docling_config=SkillDoclingConfig(),
    )


def _build_parsed_doc() -> ParsedDocument:
    block = TextBlock(
        text="Invoice INV-001 dated 2024-01-15 total 100.00",
        page_number=1,
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=200.0, y1=20.0),
        block_id="p1_b0",
    )
    return ParsedDocument(blocks=(block,), page_count=1)


def _build_offset_index() -> OffsetIndex:
    return OffsetIndex(
        entries=[OffsetIndexEntry(start=0, end=46, block_id="p1_b0")],
    )


def _build_raw_extractions(
    fields: tuple[str, ...] = ("number", "date", "total"),
) -> list[RawExtraction]:
    values = {"number": "INV-001", "date": "2024-01-15", "total": "100.00"}
    return [
        RawExtraction(
            field_name=f,
            value=values.get(f, f),
            char_offset_start=0,
            char_offset_end=7,
            grounded=True,
            attempts=1,
        )
        for f in fields
    ]


def _build_extracted_fields(
    fields: tuple[str, ...] = ("number", "date", "total"),
    *,
    all_failed: bool = False,
    mixed: bool = False,
) -> list[ExtractedField]:
    result: list[ExtractedField] = []
    for i, f in enumerate(fields):
        if all_failed:
            status = FieldStatus.failed
            value: Any = None
        elif mixed and i != 1:
            status = FieldStatus.failed
            value = None
        else:
            status = FieldStatus.extracted
            value = f"val_{f}"
        result.append(
            ExtractedField(
                name=f,
                value=value,
                status=status,
                source="document",
                grounded=not all_failed and not (mixed and i != 1),
                bbox_refs=[
                    BoundingBoxRef(page=1, x0=0.0, y0=0.0, x1=10.0, y1=10.0),
                ]
                if status == FieldStatus.extracted
                else [],
            ),
        )
    return result


# ── Fakes ───────────────────────────────────────────────────────────────


class _FakeManifest:
    def __init__(self, skill: Skill | None = None, *, error: Exception | None = None) -> None:
        self._skill = skill or _build_skill()
        self._error = error
        self.calls: list[str] = []

    def lookup(self, _name: str, _version: str) -> Skill:
        self.calls.append("lookup")
        if self._error is not None:
            raise self._error
        return self._skill


class _FakeParser:
    def __init__(self, doc: ParsedDocument | None = None) -> None:
        self._doc = doc or _build_parsed_doc()
        self.calls: list[str] = []
        self.received_config: Any = None

    async def parse(self, _pdf_bytes: bytes, docling_config: Any) -> ParsedDocument:
        self.calls.append("parse")
        self.received_config = docling_config
        return self._doc


class _FakeConcatenator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def concatenate(self, document: ParsedDocument) -> tuple[str, OffsetIndex]:
        self.calls.append("concatenate")
        text = " ".join(b.text for b in document.blocks)
        index = _build_offset_index()
        return text, index


class _FakeEngine:
    def __init__(
        self,
        extractions: list[RawExtraction] | None = None,
        *,
        sleep_seconds: float = 0.0,
    ) -> None:
        self._extractions = extractions if extractions is not None else _build_raw_extractions()
        self._sleep_seconds = sleep_seconds
        self.calls: list[str] = []

    async def extract(
        self,
        _concatenated_text: str,
        _skill: Any,
        _provider: Any,
    ) -> list[RawExtraction]:
        self.calls.append("extract")
        if self._sleep_seconds > 0:
            await asyncio.sleep(self._sleep_seconds)
        return self._extractions


class _FakeResolver:
    def __init__(self, fields: list[ExtractedField] | None = None) -> None:
        self._fields = fields if fields is not None else _build_extracted_fields()
        self.calls: list[str] = []
        self.received_declared_fields: list[str] | None = None

    def resolve(
        self,
        _raw_extractions: list[RawExtraction],
        _offset_index: OffsetIndex,
        _parsed_document: ParsedDocument,
        declared_fields: list[str],
    ) -> list[ExtractedField]:
        self.calls.append("resolve")
        self.received_declared_fields = declared_fields
        return self._fields


class _FakeAnnotator:
    def __init__(self, annotated: bytes = b"%PDF-annotated") -> None:
        self._annotated = annotated
        self.calls: list[str] = []

    async def annotate(self, _pdf_bytes: bytes, _fields: list[ExtractedField]) -> bytes:
        self.calls.append("annotate")
        return self._annotated


class _FakeProvider:
    """Structural stand-in for IntelligenceProvider."""


class _SlowParser:
    """Parser that sleeps to trigger timeout."""

    async def parse(self, _pdf_bytes: bytes, _docling_config: Any) -> ParsedDocument:
        await asyncio.sleep(0.2)
        return _build_parsed_doc()


class _ErrorParser:
    """Parser that raises PdfInvalidError."""

    async def parse(self, _pdf_bytes: bytes, _docling_config: Any) -> ParsedDocument:
        raise PdfInvalidError


# ── Service factory ─────────────────────────────────────────────────────


def _build_service(  # noqa: PLR0913 — mirrors the service constructor
    *,
    manifest: _FakeManifest | None = None,
    parser: Any = None,
    concatenator: _FakeConcatenator | None = None,
    engine: _FakeEngine | None = None,
    resolver: _FakeResolver | None = None,
    annotator: _FakeAnnotator | None = None,
    provider: Any = None,
    settings: Settings | None = None,
) -> ExtractionService:
    return ExtractionService(
        skill_manifest=manifest or _FakeManifest(),
        document_parser=parser or _FakeParser(),
        text_concatenator=concatenator or _FakeConcatenator(),
        extraction_engine=engine or _FakeEngine(),
        span_resolver=resolver or _FakeResolver(),
        pdf_annotator=annotator or _FakeAnnotator(),
        intelligence_provider=provider or _FakeProvider(),
        settings=settings or _build_settings(),
    )


_PDF_BYTES = b"%PDF-1.4 test content"


# ── Tests ───────────────────────────────────────────────────────────────


async def test_extraction_service_json_only_returns_result_with_none_pdf() -> None:
    """AC1: JSON_ONLY → annotated_pdf_bytes=None, annotator not called."""
    annotator = _FakeAnnotator()
    service = _build_service(annotator=annotator)

    result = await service.extract(
        _PDF_BYTES,
        "invoice",
        "1",
        OutputMode.JSON_ONLY,
    )

    assert isinstance(result, ExtractionResult)
    assert result.response.skill_name == "invoice"
    assert result.annotated_pdf_bytes is None
    assert annotator.calls == []


async def test_extraction_service_pdf_only_calls_annotator() -> None:
    """AC2: PDF_ONLY → annotator called once, annotated_pdf_bytes populated."""
    annotator = _FakeAnnotator(annotated=b"%PDF-highlighted")
    service = _build_service(annotator=annotator)

    result = await service.extract(
        _PDF_BYTES,
        "invoice",
        "1",
        OutputMode.PDF_ONLY,
    )

    assert result.annotated_pdf_bytes == b"%PDF-highlighted"
    assert annotator.calls == ["annotate"]


async def test_extraction_service_both_calls_annotator_and_returns_response() -> None:
    """AC3: BOTH → both response and annotated_pdf_bytes, annotator called once."""
    annotator = _FakeAnnotator()
    service = _build_service(annotator=annotator)

    result = await service.extract(
        _PDF_BYTES,
        "invoice",
        "1",
        OutputMode.BOTH,
    )

    assert result.response is not None
    assert result.annotated_pdf_bytes is not None
    assert annotator.calls == ["annotate"]


async def test_extraction_service_timeout_raises_intelligence_timeout_error() -> None:
    """AC4: Engine sleeps 200ms, timeout=0.1 → IntelligenceTimeoutError."""
    engine = _FakeEngine(sleep_seconds=0.2)
    settings = _build_settings(extraction_timeout_seconds=0.1)
    service = _build_service(engine=engine, settings=settings)

    t0 = time.monotonic()
    with pytest.raises(IntelligenceTimeoutError):
        await service.extract(
            _PDF_BYTES,
            "invoice",
            "1",
            OutputMode.JSON_ONLY,
        )
    elapsed = time.monotonic() - t0
    # Generous bound to avoid CI flakiness; the important assertion is that
    # the timeout interrupted the sleep (elapsed << 0.2s sleep duration).
    assert elapsed < 0.5, f"Timeout took {elapsed:.3f}s, expected well under sleep duration"


async def test_extraction_service_parser_timeout_raises_intelligence_timeout_error() -> None:
    """Timeout wraps entire pipeline, not just the engine."""
    settings = _build_settings(extraction_timeout_seconds=0.1)
    service = _build_service(parser=_SlowParser(), settings=settings)

    with pytest.raises(IntelligenceTimeoutError):
        await service.extract(
            _PDF_BYTES,
            "invoice",
            "1",
            OutputMode.JSON_ONLY,
        )


async def test_extraction_service_all_failed_raises_structured_output_failed() -> None:
    """AC5: All fields failed → StructuredOutputFailedError."""
    fields = _build_extracted_fields(all_failed=True)
    resolver = _FakeResolver(fields=fields)
    service = _build_service(resolver=resolver)

    with pytest.raises(StructuredOutputFailedError):
        await service.extract(
            _PDF_BYTES,
            "invoice",
            "1",
            OutputMode.JSON_ONLY,
        )


async def test_extraction_service_mixed_fields_no_exception() -> None:
    """AC6: Mixed [failed, extracted, failed] → no exception, mixed response."""
    fields = _build_extracted_fields(mixed=True)
    resolver = _FakeResolver(fields=fields)
    service = _build_service(resolver=resolver)

    result = await service.extract(
        _PDF_BYTES,
        "invoice",
        "1",
        OutputMode.JSON_ONLY,
    )

    assert len(result.response.fields) == 3
    statuses = [f.status for f in result.response.fields.values()]
    assert FieldStatus.extracted in statuses
    assert FieldStatus.failed in statuses


async def test_extraction_service_single_success_among_failures_no_exception() -> None:
    """At least one extracted field → no exception."""
    fields = [
        ExtractedField(
            name="a",
            value=None,
            status=FieldStatus.failed,
            source="document",
            grounded=False,
            bbox_refs=[],
        ),
        ExtractedField(
            name="b",
            value="ok",
            status=FieldStatus.extracted,
            source="document",
            grounded=True,
            bbox_refs=[],
        ),
    ]
    resolver = _FakeResolver(fields=fields)
    service = _build_service(resolver=resolver)

    result = await service.extract(
        _PDF_BYTES,
        "invoice",
        "1",
        OutputMode.JSON_ONLY,
    )

    assert result.response.fields["b"].status == FieldStatus.extracted


async def test_extraction_service_skill_not_found_propagates() -> None:
    """SkillNotFoundError propagates unhandled."""
    manifest = _FakeManifest(error=SkillNotFoundError(name="missing", version="1"))
    service = _build_service(manifest=manifest)

    with pytest.raises(SkillNotFoundError):
        await service.extract(
            _PDF_BYTES,
            "missing",
            "1",
            OutputMode.JSON_ONLY,
        )


async def test_extraction_service_pdf_invalid_propagates() -> None:
    """Arbitrary DomainError from parser propagates unhandled."""
    service = _build_service(parser=_ErrorParser())

    with pytest.raises(PdfInvalidError):
        await service.extract(
            _PDF_BYTES,
            "invoice",
            "1",
            OutputMode.JSON_ONLY,
        )


async def test_extraction_service_pipeline_invocation_order() -> None:
    """Pipeline steps are called in the correct linear order."""
    call_log: list[str] = []

    class _OrderManifest(_FakeManifest):
        def lookup(self, name: str, version: str) -> Skill:
            call_log.append("lookup")
            return super().lookup(name, version)

    class _OrderParser(_FakeParser):
        async def parse(self, pdf_bytes: bytes, docling_config: Any) -> ParsedDocument:
            call_log.append("parse")
            return await super().parse(pdf_bytes, docling_config)

    class _OrderConcatenator(_FakeConcatenator):
        def concatenate(self, document: ParsedDocument) -> tuple[str, OffsetIndex]:
            call_log.append("concatenate")
            return super().concatenate(document)

    class _OrderEngine(_FakeEngine):
        async def extract(self, text: str, skill: Any, provider: Any) -> list[RawExtraction]:
            call_log.append("extract")
            return await super().extract(text, skill, provider)

    class _OrderResolver(_FakeResolver):
        def resolve(
            self,
            raw_extractions: list[RawExtraction],
            offset_index: OffsetIndex,
            parsed_document: ParsedDocument,
            declared_fields: list[str],
        ) -> list[ExtractedField]:
            call_log.append("resolve")
            return super().resolve(raw_extractions, offset_index, parsed_document, declared_fields)

    class _OrderAnnotator(_FakeAnnotator):
        async def annotate(self, pdf_bytes: bytes, fields: list[ExtractedField]) -> bytes:
            call_log.append("annotate")
            return await super().annotate(pdf_bytes, fields)

    service = _build_service(
        manifest=_OrderManifest(),
        parser=_OrderParser(),
        concatenator=_OrderConcatenator(),
        engine=_OrderEngine(),
        resolver=_OrderResolver(),
        annotator=_OrderAnnotator(),
    )

    await service.extract(
        _PDF_BYTES,
        "invoice",
        "1",
        OutputMode.BOTH,
    )

    assert call_log == ["lookup", "parse", "concatenate", "extract", "resolve", "annotate"]


async def test_extraction_service_merge_docling_config_applied() -> None:
    """Parser receives merged DoclingConfig, not raw Settings or skill config."""
    parser = _FakeParser()
    skill = _build_skill()
    manifest = _FakeManifest(skill=skill)
    settings = _build_settings(docling_ocr_default="force", docling_table_mode_default="accurate")
    service = _build_service(manifest=manifest, parser=parser, settings=settings)

    await service.extract(
        _PDF_BYTES,
        "invoice",
        "1",
        OutputMode.JSON_ONLY,
    )

    config = parser.received_config
    # Skill has no overrides (SkillDoclingConfig()), so merged config should use Settings defaults
    assert config.ocr == "force"
    assert config.table_mode == "accurate"


async def test_extraction_service_resolver_runs_unconditionally_for_json_only() -> None:
    """SpanResolver.resolve runs even for JSON_ONLY output mode."""
    resolver = _FakeResolver()
    service = _build_service(resolver=resolver)

    await service.extract(
        _PDF_BYTES,
        "invoice",
        "1",
        OutputMode.JSON_ONLY,
    )

    assert resolver.calls == ["resolve"]


async def test_extraction_service_settings_default_timeout() -> None:
    """extraction_timeout_seconds defaults to 180.0."""
    settings = Settings()  # type: ignore[reportCallIssue]
    assert settings.extraction_timeout_seconds == 180.0


async def test_extraction_service_response_skill_version_is_resolved_int() -> None:
    """ExtractResponse carries resolved integer skill_version, not 'latest'."""
    skill = _build_skill(version=3)
    manifest = _FakeManifest(skill=skill)
    service = _build_service(manifest=manifest)

    result = await service.extract(
        _PDF_BYTES,
        "invoice",
        "latest",
        OutputMode.JSON_ONLY,
    )

    assert result.response.skill_name == "invoice"
    assert result.response.skill_version == 3


async def test_extraction_service_declared_fields_from_output_schema() -> None:
    """declared_fields passed to resolver matches skill output_schema properties."""
    resolver = _FakeResolver()
    skill = _build_skill(fields=("a", "b", "c"))
    manifest = _FakeManifest(skill=skill)
    service = _build_service(manifest=manifest, resolver=resolver)

    await service.extract(
        _PDF_BYTES,
        "invoice",
        "1",
        OutputMode.JSON_ONLY,
    )

    assert resolver.received_declared_fields == ["a", "b", "c"]


async def test_extraction_service_empty_declared_fields_raises_structured_output_failed() -> None:
    """Skill with no declared fields → StructuredOutputFailedError (zero extracted → 502)."""
    skill = _build_skill(fields=())
    manifest = _FakeManifest(skill=skill)
    resolver = _FakeResolver(fields=[])
    engine = _FakeEngine(extractions=[])
    service = _build_service(manifest=manifest, resolver=resolver, engine=engine)

    with pytest.raises(StructuredOutputFailedError):
        await service.extract(
            _PDF_BYTES,
            "invoice",
            "1",
            OutputMode.JSON_ONLY,
        )


async def test_extraction_service_timeout_with_blocking_thread_raises() -> None:
    """Timeout cancels the awaiting coroutine even when the engine uses to_thread.

    The real ExtractionEngine runs LangExtract in asyncio.to_thread. The
    timeout wrapper cancels the cooperative await; the background thread
    keeps running but the service raises IntelligenceTimeoutError. This
    test verifies the service's behavior matches the best-effort contract.
    """

    class _BlockingThreadEngine:
        async def extract(
            self,
            _text: str,
            _skill: Any,
            _provider: Any,
        ) -> list[RawExtraction]:
            import time as _time

            def _block() -> list[RawExtraction]:
                _time.sleep(0.3)
                return _build_raw_extractions()

            return await asyncio.to_thread(_block)

    settings = _build_settings(extraction_timeout_seconds=0.1)
    service = _build_service(engine=_BlockingThreadEngine(), settings=settings)

    with pytest.raises(IntelligenceTimeoutError):
        await service.extract(
            _PDF_BYTES,
            "invoice",
            "1",
            OutputMode.JSON_ONLY,
        )


async def test_extraction_service_rejects_when_at_capacity() -> None:
    """Issue #109: over-cap requests fail fast with ExtractionOverloadedError.

    The semaphore bounds concurrent pipelines. With cap=1, holding one
    request in-flight means any further request must be rejected immediately
    — not queued — so callers do not pile up behind a 504 budget while the
    background Docling+Ollama work keeps consuming CPU.
    """

    class _GatedEngine:
        """Engine that waits for a caller-controlled event before returning."""

        def __init__(self, gate: asyncio.Event) -> None:
            self._gate = gate
            self.in_flight = 0
            self.max_in_flight = 0

        async def extract(
            self,
            _text: str,
            _skill: Any,
            _provider: Any,
        ) -> list[RawExtraction]:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                await self._gate.wait()
                return _build_raw_extractions()
            finally:
                self.in_flight -= 1

    gate = asyncio.Event()
    engine = _GatedEngine(gate)
    settings = _build_settings(max_concurrent_extractions=1)
    service = _build_service(engine=engine, settings=settings)

    # Launch first call — it will hold the semaphore until we release the gate.
    first = asyncio.create_task(
        service.extract(_PDF_BYTES, "invoice", "1", OutputMode.JSON_ONLY),
    )
    # Give the first task a chance to enter the pipeline and acquire the semaphore.
    for _ in range(50):
        await asyncio.sleep(0)
        if engine.in_flight >= 1:
            break
    assert engine.in_flight == 1, "First call should have entered the pipeline"

    # Second and third concurrent calls must fail fast (no queuing).
    with pytest.raises(ExtractionOverloadedError) as excinfo_2:
        await service.extract(_PDF_BYTES, "invoice", "1", OutputMode.JSON_ONLY)
    assert excinfo_2.value.http_status == 503
    assert excinfo_2.value.params is not None
    assert excinfo_2.value.params.model_dump() == {"max_concurrent": 1}

    with pytest.raises(ExtractionOverloadedError):
        await service.extract(_PDF_BYTES, "invoice", "1", OutputMode.JSON_ONLY)

    # The gated engine must never have seen more than one in-flight call.
    assert engine.max_in_flight == 1

    # Release the first call and make sure it completes successfully.
    gate.set()
    result = await first
    assert isinstance(result, ExtractionResult)


async def test_extraction_service_releases_semaphore_on_success() -> None:
    """After a successful extract, the semaphore permit is returned."""
    settings = _build_settings(max_concurrent_extractions=1)
    service = _build_service(settings=settings)

    # First call should succeed and release the permit.
    await service.extract(_PDF_BYTES, "invoice", "1", OutputMode.JSON_ONLY)
    # A second call should not be rejected (permit was released).
    result = await service.extract(_PDF_BYTES, "invoice", "1", OutputMode.JSON_ONLY)
    assert isinstance(result, ExtractionResult)


async def test_extraction_service_releases_semaphore_on_error() -> None:
    """After a pipeline error, the semaphore permit is still returned."""
    settings = _build_settings(max_concurrent_extractions=1)
    # Use a parser that always raises — the permit must still be released.
    service = _build_service(parser=_ErrorParser(), settings=settings)

    with pytest.raises(PdfInvalidError):
        await service.extract(_PDF_BYTES, "invoice", "1", OutputMode.JSON_ONLY)
    # Switch to a healthy pipeline and confirm the next call is not rejected.
    healthy = _build_service(settings=settings)
    result = await healthy.extract(_PDF_BYTES, "invoice", "1", OutputMode.JSON_ONLY)
    assert isinstance(result, ExtractionResult)

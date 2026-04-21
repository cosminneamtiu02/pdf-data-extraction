"""Unit tests for the extraction router — PDFX-E006-F003.

Tests cover:
- ``read_with_byte_limit`` streaming guard (scenarios 1-4)
- ``_build_multipart_mixed`` response builder (scenarios 5-6)
- Handler output-mode branching (scenarios 7-9)
- Structured log context on ``InternalError`` raises (issue #337)
"""

from __future__ import annotations

import io

import pytest
from fastapi import UploadFile

from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extract_response import ExtractResponse
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
from app.features.extraction.schemas.field_status import FieldStatus


def _make_upload_file(data: bytes) -> UploadFile:
    """Build a FastAPI UploadFile from raw bytes."""
    return UploadFile(file=io.BytesIO(data), filename="test.pdf")


def _make_extraction_result(
    *,
    annotated_pdf_bytes: bytes | None = None,
) -> ExtractionResult:
    """Build a canned ExtractionResult for handler tests."""
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


# ---------------------------------------------------------------------------
# read_with_byte_limit
# ---------------------------------------------------------------------------


async def test_read_with_byte_limit_under_limit_returns_full_bytes() -> None:
    """Upload under the limit returns complete bytes."""
    from app.features.extraction.router import read_with_byte_limit

    data = b"x" * 512
    upload = _make_upload_file(data)
    result = await read_with_byte_limit(upload, max_bytes=4096)
    assert result == data


async def test_read_with_byte_limit_over_limit_raises_pdf_too_large() -> None:
    """Upload just over the limit raises PdfTooLargeError with correct params."""
    from app.exceptions import PdfTooLargeError
    from app.features.extraction.router import read_with_byte_limit

    max_bytes = 1024
    data = b"x" * (max_bytes + 512)
    upload = _make_upload_file(data)

    with pytest.raises(PdfTooLargeError) as exc_info:
        await read_with_byte_limit(upload, max_bytes=max_bytes)

    assert exc_info.value.params is not None
    params = exc_info.value.params.model_dump()
    assert params["max_bytes"] == max_bytes
    assert params["actual_bytes"] > max_bytes


async def test_read_with_byte_limit_empty_upload_returns_empty() -> None:
    """0-byte upload returns empty bytes without raising."""
    from app.features.extraction.router import read_with_byte_limit

    upload = _make_upload_file(b"")
    result = await read_with_byte_limit(upload, max_bytes=1024)
    assert result == b""


async def test_read_with_byte_limit_exactly_at_limit_returns_full_bytes() -> None:
    """Upload of exactly max_pdf_bytes succeeds (strict greater-than check)."""
    from app.features.extraction.router import read_with_byte_limit

    max_bytes = 1024
    data = b"x" * max_bytes
    upload = _make_upload_file(data)
    result = await read_with_byte_limit(upload, max_bytes=max_bytes)
    assert result == data


# ---------------------------------------------------------------------------
# _build_multipart_mixed
# ---------------------------------------------------------------------------


def test_multipart_builder_produces_two_parts_with_correct_headers() -> None:
    """Builder output has two parts with correct Content-Type and Content-Disposition."""
    from app.features.extraction.router import build_multipart_mixed

    json_body = b'{"skill_name":"invoice"}'
    pdf_body = b"%PDF-1.4 fake"
    body, boundary = build_multipart_mixed(json_body, pdf_body)
    body_str = body.decode("utf-8", errors="replace")

    # Verify both content types are present
    assert "Content-Type: application/json" in body_str
    assert "Content-Type: application/pdf" in body_str

    # Verify Content-Disposition headers
    assert 'Content-Disposition: form-data; name="result"' in body_str
    assert 'Content-Disposition: form-data; name="pdf"; filename="annotated.pdf"' in body_str

    # Verify CRLF line endings in the boundary structure
    assert f"--{boundary}\r\n" in body_str


def test_multipart_builder_boundary_appears_exactly_three_times() -> None:
    """Boundary appears as opener, separator, and closer — exactly three times."""
    from app.features.extraction.router import build_multipart_mixed

    json_body = b'{"key":"value"}'
    pdf_body = b"%PDF-1.4 content"
    body, boundary = build_multipart_mixed(json_body, pdf_body)
    body_str = body.decode("utf-8", errors="replace")

    # Count the opener and separator (--boundary\r\n) and the closer (--boundary--)
    opener_sep_count = body_str.count(f"--{boundary}\r\n")
    closer_count = body_str.count(f"--{boundary}--")
    assert opener_sep_count == 2, f"expected 2 opener/separator, got {opener_sep_count}"
    assert closer_count == 1, f"expected 1 closer, got {closer_count}"


# ---------------------------------------------------------------------------
# Handler output-mode branching
# ---------------------------------------------------------------------------


def test_handler_json_only_returns_json_response() -> None:
    """JSON_ONLY mode: returns JSONResponse with model_dump content."""
    from fastapi.responses import JSONResponse

    from app.features.extraction.router import _serialize_result
    from app.features.extraction.schemas.output_mode import OutputMode

    result = _make_extraction_result(annotated_pdf_bytes=None)
    response = _serialize_result(result, OutputMode.JSON_ONLY)

    assert isinstance(response, JSONResponse)
    assert response.media_type == "application/json"


def test_handler_pdf_only_returns_pdf_response() -> None:
    """PDF_ONLY mode: returns Response with application/pdf media type."""
    from fastapi import Response

    from app.features.extraction.router import _serialize_result
    from app.features.extraction.schemas.output_mode import OutputMode

    pdf_bytes = b"%PDF-1.4 annotated content"
    result = _make_extraction_result(annotated_pdf_bytes=pdf_bytes)
    response = _serialize_result(result, OutputMode.PDF_ONLY)

    assert isinstance(response, Response)
    assert response.media_type == "application/pdf"
    assert response.body == pdf_bytes


def test_handler_pdf_only_raises_internal_error_when_bytes_none() -> None:
    """PDF_ONLY mode raises InternalError when annotated_pdf_bytes is None."""
    from app.exceptions import InternalError
    from app.features.extraction.router import _serialize_result
    from app.features.extraction.schemas.output_mode import OutputMode

    result = _make_extraction_result(annotated_pdf_bytes=None)

    with pytest.raises(InternalError):
        _serialize_result(result, OutputMode.PDF_ONLY)


def test_handler_both_returns_multipart_mixed_response() -> None:
    """BOTH mode: returns Response with multipart/mixed media type."""
    from fastapi import Response

    from app.features.extraction.router import _serialize_result
    from app.features.extraction.schemas.output_mode import OutputMode

    pdf_bytes = b"%PDF-1.4 annotated content"
    result = _make_extraction_result(annotated_pdf_bytes=pdf_bytes)
    response = _serialize_result(result, OutputMode.BOTH)

    assert isinstance(response, Response)
    assert response.media_type is not None
    assert response.media_type.startswith('multipart/mixed; boundary="')


def test_handler_both_raises_internal_error_when_bytes_none() -> None:
    """BOTH mode raises InternalError when annotated_pdf_bytes is None."""
    from app.exceptions import InternalError
    from app.features.extraction.router import _serialize_result
    from app.features.extraction.schemas.output_mode import OutputMode

    result = _make_extraction_result(annotated_pdf_bytes=None)

    with pytest.raises(InternalError):
        _serialize_result(result, OutputMode.BOTH)


# ---------------------------------------------------------------------------
# Structured log context on InternalError raises (issue #337)
# ---------------------------------------------------------------------------


class _SpyLogger:
    """Test double for ``router_module._logger`` (Copilot-review #465).

    Why we don't use ``structlog.testing.capture_logs()`` here: the router
    module defines ``_logger = structlog.get_logger(__name__)`` at import
    time, and our ``configure_logging()`` registers
    ``cache_logger_on_first_use=True``. Whichever test first touches the
    router's ``_logger`` outside a ``capture_logs()`` context can cause
    structlog to cache a bound logger that subsequent ``capture_logs()``
    contexts won't see — making log-assertion tests order-dependent. A
    direct monkeypatched spy sidesteps structlog's global state entirely,
    the same pattern used in
    ``tests/unit/features/extraction/test_extraction_service.py``.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:  # pragma: no cover
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:  # pragma: no cover
        self.events.append((event, kwargs))

    def error(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))


def test_handler_pdf_only_emits_log_context_before_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF_ONLY invariant violation emits a structured log event before raising.

    Issue #337: ``raise InternalError()`` in ``_serialize_result`` was silent —
    operators reading a 500 had no idea whether it was a bug or a dependency
    issue. The fix attaches structlog context (event name, output_mode,
    has_annotated_pdf) immediately before the raise.
    """
    from app.exceptions import InternalError
    from app.features.extraction import router as router_module
    from app.features.extraction.router import _serialize_result
    from app.features.extraction.schemas.output_mode import OutputMode

    spy = _SpyLogger()
    monkeypatch.setattr(router_module, "_logger", spy)
    result = _make_extraction_result(annotated_pdf_bytes=None)

    with pytest.raises(InternalError):
        _serialize_result(result, OutputMode.PDF_ONLY)

    event = next(
        (
            kwargs
            for name, kwargs in spy.events
            if name == "router_serialization_invariant_violated"
        ),
        None,
    )
    assert event is not None, (
        f"expected 'router_serialization_invariant_violated' log event, got {spy.events!r}"
    )
    assert event["output_mode"] == OutputMode.PDF_ONLY.value
    assert event["has_annotated_pdf"] is False


def test_handler_both_emits_log_context_before_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BOTH invariant violation emits a structured log event before raising."""
    from app.exceptions import InternalError
    from app.features.extraction import router as router_module
    from app.features.extraction.router import _serialize_result
    from app.features.extraction.schemas.output_mode import OutputMode

    spy = _SpyLogger()
    monkeypatch.setattr(router_module, "_logger", spy)
    result = _make_extraction_result(annotated_pdf_bytes=None)

    with pytest.raises(InternalError):
        _serialize_result(result, OutputMode.BOTH)

    event = next(
        (
            kwargs
            for name, kwargs in spy.events
            if name == "router_serialization_invariant_violated"
        ),
        None,
    )
    assert event is not None, (
        f"expected 'router_serialization_invariant_violated' log event, got {spy.events!r}"
    )
    assert event["output_mode"] == OutputMode.BOTH.value
    assert event["has_annotated_pdf"] is False

"""Contract tests — validates OpenAPI spec compliance.

The original two tests exercise ``/openapi.json`` well-formedness and
``/health``. Issue #117 extended this module with schemathesis-driven
contract assertions against ``POST /api/v1/extract`` so that every status
code declared in the OpenAPI spec for that operation is proven reachable
**and** the actual response envelope is validated against the declared
schema for that status code by schemathesis itself
(``schema["/api/v1/extract"]["POST"].validate_response(response)``).

Why targeted (non-``@schema.parametrize``) tests?
-------------------------------------------------
``/api/v1/extract`` requires a multipart upload with a real PDF byte
stream. Schemathesis' property-based generator cannot synthesize valid
PDFs from the OpenAPI ``format: binary`` declaration. Rather than wire a
custom ``schemathesis.openapi.media_type`` generator (which still could
not drive the handler into each error branch deterministically), each
status code is exercised by one hand-rolled request that uses a
``dependency_overrides`` stub on ``get_extraction_service`` to raise the
corresponding ``DomainError``. The real schemathesis conformance check
runs on the response via ``validate_response`` — the same validator the
parametrized path uses — so the OpenAPI contract is enforced for every
declared status code.

Stubbing approach
-----------------
Every test builds an app via ``create_app(Settings(skills_dir=tmp_path))``
and installs ``app.dependency_overrides[get_extraction_service]`` with
an ``AsyncMock(spec=ExtractionService)`` whose ``extract`` method either
returns a canned ``ExtractionResult`` (200) or raises the target
``DomainError`` (4xx/5xx). Docling, LangExtract, Ollama and PyMuPDF are
never loaded because the service itself is replaced. The 413 PDF_TOO_LARGE
path is driven by a small ``max_pdf_bytes`` override on ``Settings`` so
the router-level byte-size guard fires before the stub is even called —
matching how the real handler short-circuits the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest
import schemathesis
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from app.api.deps import get_extraction_service
from app.exceptions import (
    IntelligenceTimeoutError,
    IntelligenceUnavailableError,
    PdfInvalidError,
    PdfNoTextExtractableError,
    PdfPasswordProtectedError,
    PdfTooManyPagesError,
    SkillNotFoundError,
    StructuredOutputFailedError,
)
from app.features.extraction.service import ExtractionService
from app.main import app, create_app
from tests.contract._helpers import (
    make_canned_result as _make_canned_result,
)
from tests.contract._helpers import (
    settings as _settings,
)
from tests.contract._helpers import (
    write_valid_skill as _write_valid_skill,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Existing /openapi.json + /health conformance checks
# ---------------------------------------------------------------------------


def test_openapi_spec_is_valid() -> None:
    """The OpenAPI spec should be valid and contain the base endpoints."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/openapi.json")
    assert response.status_code == 200

    spec = response.json()
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "PDF Data Extraction API"

    paths = spec["paths"]

    # Health endpoints at root
    assert "/health" in paths
    assert "/ready" in paths


def test_health_endpoint_conforms_to_spec() -> None:
    """Health endpoint should return the expected shape."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/v1/extract contract coverage (issue #117)
# ---------------------------------------------------------------------------

_FIXTURE_PDF = Path(__file__).resolve().parent.parent / "fixtures" / "pdfs" / "native_two_page.pdf"


def _load_pdf_bytes() -> bytes:
    """Load the fixture PDF used by every /extract contract case."""
    return _FIXTURE_PDF.read_bytes()


def _build_app_with_stub(
    tmp_path: Path,
    stub: AsyncMock,
    **settings_overrides: object,
) -> FastAPI:
    """Create a test app whose ExtractionService is the provided ``stub``."""
    _write_valid_skill(tmp_path)
    built = create_app(_settings(tmp_path, **settings_overrides))
    built.dependency_overrides[get_extraction_service] = lambda: stub
    return built


async def _post_extract(  # noqa: PLR0913 — kwargs-only test helper with per-case overrides
    app_under_test: FastAPI,
    *,
    skill_name: str = "invoice",
    skill_version: str = "1",
    output_mode: str = "JSON_ONLY",
    pdf_bytes: bytes | None = None,
    pdf_filename: str = "fixture.pdf",
    include_output_mode: bool = True,
) -> httpx.Response:
    """POST /api/v1/extract against ``app_under_test`` via ASGITransport.

    The multipart request body is eagerly read into memory before the
    request is sent. ``schemathesis.APIOperation.validate_response`` reads
    ``response.request.content`` post-send to rebuild the request for its
    request-level checks; if the underlying ``httpx.Request`` still has a
    streaming body (``_content`` unset) it raises ``httpx.RequestNotRead``.
    Calling ``request.read()`` before ``client.send(request)`` materializes
    the multipart body so schemathesis can inspect it afterward.
    """
    body = _load_pdf_bytes() if pdf_bytes is None else pdf_bytes
    data: dict[str, str] = {
        "skill_name": skill_name,
        "skill_version": skill_version,
    }
    if include_output_mode:
        data["output_mode"] = output_mode
    transport = ASGITransport(app=app_under_test)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        request = client.build_request(
            "POST",
            "/api/v1/extract",
            data=data,
            files={"pdf": (pdf_filename, body, "application/pdf")},
        )
        request.read()  # materialize multipart body for schemathesis inspection
        return await client.send(request)


# The `extract_schema` fixture lives in `tests/contract/conftest.py` and
# is session-scoped per issue #352 (previously module-scoped here, which
# still rebuilt the schema for every contract module and — prior to the
# #352 fix — leaked a `tempfile.mkdtemp` skills directory per invocation).


def _validate(
    schema: schemathesis.BaseSchema,
    response: httpx.Response,
) -> None:
    """Assert ``response`` conforms to the schemathesis contract for /extract."""
    # ``validate_response`` raises ``FailureGroup`` (or ``AssertionError``)
    # on any drift — status code not declared, body not matching the
    # declared schema, wrong media type, etc. Letting it raise surfaces
    # the first failure directly in pytest output, which is the intended
    # UX for a contract test.
    # `APIOperation.validate_response` accepts `httpx.Response` directly
    # per schemathesis v4's type signature, so no adapter is needed.
    schema["/api/v1/extract"]["POST"].validate_response(response)


@pytest.fixture
def canned_success_stub() -> AsyncMock:
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.return_value = _make_canned_result()
    return stub


async def test_extract_200_conforms_to_openapi_schema(
    tmp_path: Path,
    canned_success_stub: AsyncMock,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """200 JSON_ONLY response validates against the schemathesis contract."""
    app_under_test = _build_app_with_stub(tmp_path, canned_success_stub)
    response = await _post_extract(app_under_test)

    assert response.status_code == 200
    _validate(extract_schema, response)


async def test_extract_400_pdf_invalid_conforms_to_openapi_schema(
    tmp_path: Path,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """400 PDF_INVALID response validates against the schemathesis contract."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.side_effect = PdfInvalidError()
    app_under_test = _build_app_with_stub(tmp_path, stub)

    response = await _post_extract(app_under_test)

    assert response.status_code == 400
    _validate(extract_schema, response)


async def test_extract_400_pdf_password_protected_conforms_to_openapi_schema(
    tmp_path: Path,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """400 PDF_PASSWORD_PROTECTED response validates against the schemathesis contract."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.side_effect = PdfPasswordProtectedError()
    app_under_test = _build_app_with_stub(tmp_path, stub)

    response = await _post_extract(app_under_test)

    assert response.status_code == 400
    _validate(extract_schema, response)


async def test_extract_404_skill_not_found_conforms_to_openapi_schema(
    tmp_path: Path,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """404 SKILL_NOT_FOUND response validates against the schemathesis contract."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.side_effect = SkillNotFoundError(name="nonexistent", version="1")
    app_under_test = _build_app_with_stub(tmp_path, stub)

    response = await _post_extract(app_under_test, skill_name="nonexistent")

    assert response.status_code == 404
    _validate(extract_schema, response)


async def test_extract_413_pdf_too_large_conforms_to_openapi_schema(
    tmp_path: Path,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """413 PDF_TOO_LARGE response validates against the schemathesis contract.

    Driven by ``max_pdf_bytes=64`` so the fixture PDF trips the router's
    byte-size guard before the stub is invoked.
    """
    stub = AsyncMock(spec=ExtractionService)
    app_under_test = _build_app_with_stub(tmp_path, stub, max_pdf_bytes=64)

    response = await _post_extract(app_under_test)

    assert response.status_code == 413
    _validate(extract_schema, response)


async def test_extract_413_pdf_too_many_pages_conforms_to_openapi_schema(
    tmp_path: Path,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """413 PDF_TOO_MANY_PAGES response validates against the schemathesis contract."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.side_effect = PdfTooManyPagesError(limit=5, actual=42)
    app_under_test = _build_app_with_stub(tmp_path, stub)

    response = await _post_extract(app_under_test)

    assert response.status_code == 413
    _validate(extract_schema, response)


async def test_extract_422_request_validation_conforms_to_openapi_schema(
    tmp_path: Path,
    canned_success_stub: AsyncMock,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """422 VALIDATION_FAILED envelope (missing form field) validates against the contract."""
    app_under_test = _build_app_with_stub(tmp_path, canned_success_stub)

    # Omit ``output_mode`` so FastAPI raises RequestValidationError, which
    # the custom handler serializes through the ValidationFailedError envelope.
    response = await _post_extract(app_under_test, include_output_mode=False)

    assert response.status_code == 422
    _validate(extract_schema, response)


async def test_extract_422_no_extractable_text_conforms_to_openapi_schema(
    tmp_path: Path,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """422 PDF_NO_TEXT_EXTRACTABLE response validates against the schemathesis contract."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.side_effect = PdfNoTextExtractableError()
    app_under_test = _build_app_with_stub(tmp_path, stub)

    response = await _post_extract(app_under_test)

    assert response.status_code == 422
    _validate(extract_schema, response)


async def test_extract_502_structured_output_failed_conforms_to_openapi_schema(
    tmp_path: Path,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """502 STRUCTURED_OUTPUT_FAILED response validates against the schemathesis contract."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.side_effect = StructuredOutputFailedError()
    app_under_test = _build_app_with_stub(tmp_path, stub)

    response = await _post_extract(app_under_test)

    assert response.status_code == 502
    _validate(extract_schema, response)


async def test_extract_503_intelligence_unavailable_conforms_to_openapi_schema(
    tmp_path: Path,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """503 INTELLIGENCE_UNAVAILABLE response validates against the schemathesis contract."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.side_effect = IntelligenceUnavailableError()
    app_under_test = _build_app_with_stub(tmp_path, stub)

    response = await _post_extract(app_under_test)

    assert response.status_code == 503
    _validate(extract_schema, response)


async def test_extract_504_intelligence_timeout_conforms_to_openapi_schema(
    tmp_path: Path,
    extract_schema: schemathesis.BaseSchema,
) -> None:
    """504 INTELLIGENCE_TIMEOUT response validates against the schemathesis contract."""
    stub = AsyncMock(spec=ExtractionService)
    stub.extract.side_effect = IntelligenceTimeoutError(budget_seconds=180.0)
    app_under_test = _build_app_with_stub(tmp_path, stub)

    response = await _post_extract(app_under_test)

    assert response.status_code == 504
    _validate(extract_schema, response)

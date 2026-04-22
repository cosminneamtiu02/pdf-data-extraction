"""Extraction router — ``POST /api/v1/extract``.

This module is the thin HTTP shell at the top of the extraction feature's
vertical slice.  It parses the multipart form, enforces the byte-size guard
before the expensive PDF-parsing pipeline (Docling, LLM, annotation) is
allocated, delegates to ``ExtractionService.extract``, and serializes the
result into the right HTTP response shape per ``output_mode``.  There is no
business logic here.

Note: the byte-size guard runs *after* Starlette has parsed the multipart
form and spooled the upload to a temp file.  It does not reject at the
HTTP framing level.  What it prevents is the downstream Docling +
extraction pipeline from being invoked on an oversized document.

The multipart/mixed builder is a generator that yields framing and body
chunks without concatenating them (issue #387): concatenating
``header + json + pdf + footer`` into one ``bytes`` blob would roughly
double worker RSS per BOTH-mode request, and under
``max_concurrent_extractions`` simultaneous requests that scales linearly.
``read_with_byte_limit`` is a public async helper (tested directly by unit
tests) that reads the upload in 1 MB chunks and aborts early on the first
chunk that pushes the total over ``Settings.max_pdf_bytes``.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Annotated, NoReturn, assert_never

import structlog
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import get_extraction_service, get_settings
from app.core.config import (
    Settings,  # noqa: TC001  # runtime: FastAPI resolves Annotated[..., Depends()]
)
from app.exceptions import InternalError, PdfTooLargeError
from app.features.extraction.schemas.extract_request import SKILL_VERSION_PATTERN
from app.features.extraction.schemas.output_mode import OutputMode
from app.features.extraction.service import ExtractionService  # noqa: TC001  # runtime: FastAPI DI
from app.schemas.error_response import ErrorResponse

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.features.extraction.extraction_result import ExtractionResult

_CHUNK_SIZE = 1024 * 1024  # 1 MB

_logger = structlog.get_logger(__name__)


router = APIRouter(tags=["extraction"])


# ---------------------------------------------------------------------------
# Public helpers (tested directly by unit tests)
# ---------------------------------------------------------------------------


async def read_with_byte_limit(upload: UploadFile, max_bytes: int) -> bytes:
    """Read *upload* in chunks, raising ``PdfTooLargeError`` on overflow.

    The guard aborts on the **first** chunk that pushes the accumulated total
    past *max_bytes*.  This is a strict greater-than check: an upload of
    exactly *max_bytes* is accepted.
    """
    buf = bytearray()

    while True:
        chunk = await upload.read(_CHUNK_SIZE)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise PdfTooLargeError(max_bytes=max_bytes, actual_bytes=len(buf))

    return bytes(buf)


def build_multipart_mixed(json_body: bytes, pdf_body: bytes) -> tuple[Iterator[bytes], str]:
    """Build a streaming ``multipart/mixed`` response body with two parts.

    Returns ``(chunk_iterator, boundary_string)``.  The iterator yields each
    framing chunk, then the JSON body, then the separator, then the PDF body
    by identity, then the terminator — WITHOUT concatenating them into a
    single ``bytes`` blob (issue #387).  Uses CRLF line endings per the
    multipart RFC.

    The PDF body is yielded as-is (identity-preserved) so the memory
    footprint of a BOTH-mode response is roughly ``PDF size + small framing``
    rather than ``2 x PDF size`` that concatenation would cost.
    """
    boundary = secrets.token_hex(16)
    crlf = "\r\n"
    header = (
        f"--{boundary}{crlf}"
        f'Content-Disposition: form-data; name="result"{crlf}'
        f"Content-Type: application/json{crlf}"
        f"{crlf}"
    ).encode()
    separator = (
        f"{crlf}"
        f"--{boundary}{crlf}"
        f'Content-Disposition: form-data; name="pdf"; filename="annotated.pdf"{crlf}'
        f"Content-Type: application/pdf{crlf}"
        f"{crlf}"
    ).encode()
    footer = f"{crlf}--{boundary}--{crlf}".encode()

    def _chunks() -> Iterator[bytes]:
        yield header
        yield json_body
        yield separator
        yield pdf_body
        yield footer

    return _chunks(), boundary


def _raise_missing_annotated_pdf(output_mode: OutputMode) -> NoReturn:
    """Log the router-local context and raise ``InternalError``.

    Factored out so the PDF_ONLY and BOTH branches cannot drift on the event
    name or field shape. See issue #337 for why the log must precede the raise.
    The ``NoReturn`` annotation lets pyright narrow ``annotated_pdf_bytes`` to
    non-``None`` after the guarded call.
    """
    _logger.error(
        "router_serialization_invariant_violated",
        output_mode=output_mode.value,
        has_annotated_pdf=False,
    )
    raise InternalError()  # noqa: RSE102  # explicit instantiation for consistency


def _serialize_result(result: ExtractionResult, output_mode: OutputMode) -> Response:
    """Serialize *result* into the right HTTP response per *output_mode*.

    ``raise InternalError()`` is defensive against a service-layer invariant
    violation: for any ``output_mode`` other than ``JSON_ONLY``,
    ``ExtractionService.extract`` must populate ``annotated_pdf_bytes``. If
    that contract is ever broken, the ``InternalError`` surfaces as a generic
    500 via the top-level exception handler, and operators reading the error
    response have no idea whether the fault is a service bug or an upstream
    dependency issue. Emit a structured log event immediately before the raise
    so the feature-local context (the requested ``output_mode`` and the fact
    that ``annotated_pdf_bytes`` was ``None``) is preserved for diagnosis — the
    top-level handler only sees the ``InternalError`` and cannot reconstruct
    the router-local context after the fact. See issue #337.
    """
    if output_mode == OutputMode.JSON_ONLY:
        return JSONResponse(content=result.response.model_dump(mode="json"))

    if output_mode == OutputMode.PDF_ONLY:
        if result.annotated_pdf_bytes is None:
            _raise_missing_annotated_pdf(output_mode)
        return Response(content=result.annotated_pdf_bytes, media_type="application/pdf")

    if output_mode == OutputMode.BOTH:
        if result.annotated_pdf_bytes is None:
            _raise_missing_annotated_pdf(output_mode)
        json_bytes = result.response.model_dump_json().encode()
        chunks, boundary = build_multipart_mixed(json_bytes, result.annotated_pdf_bytes)
        return StreamingResponse(
            chunks,
            media_type=f'multipart/mixed; boundary="{boundary}"',
        )

    assert_never(output_mode)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/extract",
    responses={
        # A 200 response is multi-mediaType: the Content-Type on the wire is
        # determined by the submitted ``output_mode``. We advertise the
        # non-JSON variants here so generated clients and schemathesis see
        # the real contract. This route does not declare a FastAPI
        # ``response_model``; JSON responses (for ``OutputMode.JSON_ONLY``)
        # are produced directly by the handler via ``JSONResponse``. The
        # other two output modes are opaque binary bodies, declared as raw
        # ``content`` with an empty schema per the FastAPI docs.
        200: {
            "description": (
                "Extraction succeeded. Content-Type depends on output_mode: "
                "application/json (JSON_ONLY), application/pdf (PDF_ONLY), "
                "or multipart/mixed (BOTH)."
            ),
            "content": {
                "application/pdf": {},
                "multipart/mixed": {},
            },
        },
        # Every error envelope is the DomainError ``ErrorResponse`` shape
        # produced by ``app.api.errors.register_exception_handlers``. The
        # 422 entry overrides FastAPI's default ``HTTPValidationError`` so
        # the advertised contract matches what the custom
        # ``RequestValidationError`` handler actually emits.
        400: {
            "description": "PDF is invalid or password-protected",
            "model": ErrorResponse,
        },
        404: {
            "description": "Requested skill (name, version) is not registered",
            "model": ErrorResponse,
        },
        413: {
            "description": "PDF exceeds max_pdf_bytes or max_pages",
            "model": ErrorResponse,
        },
        422: {
            "description": (
                "Request validation failed, or the PDF yielded no extractable text even after OCR"
            ),
            "model": ErrorResponse,
        },
        502: {
            "description": (
                "Structured output extraction failed for every declared field, "
                "or the skill declared zero fields and zero fields were extracted"
            ),
            "model": ErrorResponse,
        },
        503: {
            "description": (
                "Intelligence backend (Ollama) is unavailable, or the extraction "
                "service is already at its configured concurrency cap"
            ),
            "model": ErrorResponse,
        },
        504: {
            "description": "Extraction pipeline timed out",
            "model": ErrorResponse,
        },
    },
)
async def extract(  # noqa: PLR0913  # FastAPI DI handler — each param is an injected dependency
    pdf: Annotated[UploadFile, File()],
    skill_name: Annotated[str, Form()],
    skill_version: Annotated[str, Form(pattern=SKILL_VERSION_PATTERN)],
    output_mode: Annotated[OutputMode, Form()],
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> Response:
    """Accept a PDF and skill parameters, run extraction, return per output mode."""
    pdf_bytes = await read_with_byte_limit(pdf, settings.max_pdf_bytes)
    result = await service.extract(pdf_bytes, skill_name, skill_version, output_mode)
    return _serialize_result(result, output_mode)

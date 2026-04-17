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

The multipart/mixed builder (~15 lines) is inlined because it has exactly
one consumer.  ``read_with_byte_limit`` is a public async helper (tested
directly by unit tests) that reads the upload in 1 MB chunks and aborts
early on the first chunk that pushes the total over
``Settings.max_pdf_bytes``.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Annotated, assert_never

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.responses import JSONResponse

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
    from app.features.extraction.extraction_result import ExtractionResult

_CHUNK_SIZE = 1024 * 1024  # 1 MB


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


def build_multipart_mixed(json_body: bytes, pdf_body: bytes) -> tuple[bytes, str]:
    """Build a ``multipart/mixed`` response body with two parts.

    Returns ``(body_bytes, boundary_string)``.  Uses CRLF line endings per
    the multipart RFC.
    """
    boundary = secrets.token_hex(16)
    crlf = "\r\n"
    parts = (
        (
            f"--{boundary}{crlf}"
            f'Content-Disposition: form-data; name="result"{crlf}'
            f"Content-Type: application/json{crlf}"
            f"{crlf}"
        ).encode()
        + json_body
        + (
            f"{crlf}"
            f"--{boundary}{crlf}"
            f'Content-Disposition: form-data; name="pdf"; filename="annotated.pdf"{crlf}'
            f"Content-Type: application/pdf{crlf}"
            f"{crlf}"
        ).encode()
        + pdf_body
        + f"{crlf}--{boundary}--{crlf}".encode()
    )

    return parts, boundary


def _serialize_result(result: ExtractionResult, output_mode: OutputMode) -> Response:
    """Serialize *result* into the right HTTP response per *output_mode*."""
    if output_mode == OutputMode.JSON_ONLY:
        return JSONResponse(content=result.response.model_dump(mode="json"))

    if output_mode == OutputMode.PDF_ONLY:
        if result.annotated_pdf_bytes is None:
            raise InternalError()  # noqa: RSE102  # explicit instantiation for consistency
        return Response(content=result.annotated_pdf_bytes, media_type="application/pdf")

    if output_mode == OutputMode.BOTH:
        if result.annotated_pdf_bytes is None:
            raise InternalError()  # noqa: RSE102
        json_bytes = result.response.model_dump_json().encode()
        body, boundary = build_multipart_mixed(json_bytes, result.annotated_pdf_bytes)
        return Response(content=body, media_type=f'multipart/mixed; boundary="{boundary}"')

    assert_never(output_mode)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/extract",
    responses={
        # A 200 response is multi-mediaType: the Content-Type on the wire is
        # determined by the submitted ``output_mode``. We advertise all three
        # so generated clients and schemathesis see the real contract. The
        # JSON body is ``ExtractResponse`` (default response_model). The
        # other two are opaque binary bodies, declared as raw ``content``
        # with an empty schema per the FastAPI docs.
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
            "description": "Structured output extraction failed for every declared field",
            "model": ErrorResponse,
        },
        503: {
            "description": "Intelligence backend (Ollama) is unavailable",
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

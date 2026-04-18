"""Integration tests for the ASGI-level upload-size guard (issue #112).

Starlette's multipart parser spools the full request body into memory/disk
BEFORE the route handler runs. The existing ``read_with_byte_limit`` inside
the handler only rejects AFTER that spooling has already cost us ingress
resources. The ``UploadSizeLimitMiddleware`` is an ASGI-level gate wired in
``create_app`` that inspects ``Content-Length`` and rejects oversized
uploads before Starlette ever touches the body.

These tests assert the end-to-end contract through the real ``create_app``
middleware stack — ``CORS -> RequestId -> AccessLog -> UploadSizeLimit``
— so that a regression anywhere in the chain (registration order, a
silently-dropped middleware, mis-scoped ``guarded_paths``) trips CI.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import yaml
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_extraction_service
from app.core.config import Settings
from app.features.extraction.service import ExtractionService
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


def _settings(skills_dir: Path, **overrides: object) -> Settings:
    return Settings(skills_dir=skills_dir, app_env="development", **overrides)  # type: ignore[reportCallIssue]


def _stub_service() -> ExtractionService:
    return AsyncMock(spec=ExtractionService)


def _build_app(tmp_path: Path, stub: ExtractionService, **settings_overrides: object):
    _write_valid_skill(tmp_path)
    app = create_app(_settings(tmp_path, **settings_overrides))
    app.dependency_overrides[get_extraction_service] = lambda: stub
    return app


async def test_asgi_guard_rejects_oversized_upload_before_service(tmp_path: Path) -> None:
    """An oversized Content-Length produces 413 PDF_TOO_LARGE and the
    ``ExtractionService`` is never invoked (i.e. the multipart body was
    never parsed).

    The production wiring inflates the ASGI threshold by a 64 KiB
    multipart-overhead allowance on top of ``Settings.max_pdf_bytes``, so
    the body must exceed ``max_pdf_bytes + overhead`` to trip the guard.
    The error envelope still advertises ``max_pdf_bytes`` (the
    authoritative PDF limit clients need to know), not the inflated
    threshold.
    """
    stub = _stub_service()
    max_pdf_bytes = 1024
    # Overhead in prod wiring; matches ``_MULTIPART_OVERHEAD_ALLOWANCE_BYTES``
    # in ``app/api/middleware.py``. Mirrored locally rather than imported
    # since the constant is internal to the wiring module.
    overhead = 64 * 1024
    body_size = max_pdf_bytes + overhead + 2048
    app = _build_app(tmp_path, stub, max_pdf_bytes=max_pdf_bytes)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Send a raw body larger than ``max_pdf_bytes + overhead``. httpx
        # sets Content-Length automatically based on ``content`` length;
        # the middleware must reject based on that header before the route
        # is dispatched.
        response = await client.post(
            "/api/v1/extract",
            content=b"x" * body_size,
            headers={"content-type": "multipart/form-data; boundary=b"},
        )

    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "PDF_TOO_LARGE"
    # Envelope reports the authoritative PDF limit, NOT the inflated ASGI
    # threshold — clients should see the real constraint they're violating.
    assert body["error"]["params"]["max_bytes"] == max_pdf_bytes
    assert body["error"]["params"]["actual_bytes"] == body_size
    # Correlation id round-trips through the production middleware stack.
    assert body["error"]["request_id"] == response.headers["x-request-id"]

    # Crucially: the service mock was never called — confirming the guard
    # ran before route dispatch and before multipart spooling.
    stub.extract.assert_not_called()


async def test_asgi_guard_allows_pdf_at_limit_with_multipart_overhead(tmp_path: Path) -> None:
    """A real multipart upload whose PDF part is exactly ``max_pdf_bytes``
    bytes passes the ASGI guard even though the total Content-Length is
    larger than ``max_pdf_bytes`` (multipart boundary markers,
    Content-Disposition headers, field separators all add overhead).

    Before the overhead allowance was added, the ASGI threshold was set
    to ``max_pdf_bytes`` verbatim; any legitimate multipart body carrying
    a max-sized PDF would exceed it and be rejected at the ASGI layer,
    denying valid uploads. This test pins the opposite invariant: the
    ASGI guard lets the request through even though Content-Length
    strictly exceeds ``max_pdf_bytes``.
    """
    # Stub the service to raise a known downstream error. Any non-413
    # response confirms the ASGI guard passed the request through —
    # whether it's the 400 from our error or something else — the point
    # is that the ASGI layer did NOT reject it. Using ``side_effect`` is
    # cheaper than assembling a valid ``ExtractionResult``.
    from app.exceptions import PdfInvalidError

    stub = _stub_service()
    stub.extract.side_effect = PdfInvalidError()
    max_pdf_bytes = 1024
    app = _build_app(tmp_path, stub, max_pdf_bytes=max_pdf_bytes)
    transport = ASGITransport(app=app)

    # Build a real multipart body (data= + files=) so Content-Length
    # includes the full envelope overhead, not just the PDF bytes. This
    # is the only way to exercise the overhead-allowance behavior — a
    # raw ``content=`` body would have CL == payload length exactly.
    pdf_bytes = b"x" * max_pdf_bytes
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        request = client.build_request(
            "POST",
            "/api/v1/extract",
            data={"skill_name": "invoice", "skill_version": "1", "output_mode": "JSON_ONLY"},
            files={"pdf": ("at_limit.pdf", pdf_bytes, "application/pdf")},
        )
        request.read()
        content_length_header = request.headers.get("content-length")
        response = await client.send(request)

    # Sanity: the assembled multipart body is strictly larger than the
    # PDF-bytes limit. If this precondition ever breaks, the test stops
    # exercising the overhead-allowance path.
    assert content_length_header is not None
    total_body_size = int(content_length_header)
    assert total_body_size > max_pdf_bytes, (
        f"Test precondition failed: multipart body ({total_body_size} bytes) "
        f"should exceed max_pdf_bytes ({max_pdf_bytes}) for the overhead "
        "allowance to be meaningfully exercised."
    )

    # ASGI-layer rejection manifests as 413 with PDF_TOO_LARGE envelope.
    # Downstream responses may still be non-200 (skill stub returned
    # something, router validation, etc.) but they must not be the
    # ASGI rejection.
    if response.status_code == 413:
        body = response.json()
        assert body["error"]["code"] != "PDF_TOO_LARGE", (
            f"ASGI guard rejected a {max_pdf_bytes}-byte PDF inside a "
            f"{total_body_size}-byte multipart envelope — overhead allowance "
            "is too small to admit a legitimate max-sized upload."
        )


async def test_asgi_guard_does_not_affect_health_endpoint(tmp_path: Path) -> None:
    """The health endpoint is outside ``guarded_paths`` and is not subject
    to the upload-size check."""
    stub = _stub_service()
    app = _build_app(tmp_path, stub, max_pdf_bytes=1024)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"content-length": "999999"},
        )

    assert response.status_code == 200


async def test_asgi_guard_allows_under_limit_upload_through(tmp_path: Path) -> None:
    """An upload at or below the limit reaches the handler unimpeded.

    This is the happy-path complement to
    ``test_asgi_guard_rejects_oversized_upload_before_service``; a regression
    that made the guard reject *everything* (wrong comparison operator,
    typo on ``max_bytes``) would otherwise slip past the 413 test alone.
    """
    from app.features.extraction.extraction_result import ExtractionResult
    from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
    from app.features.extraction.schemas.extract_response import ExtractResponse
    from app.features.extraction.schemas.extracted_field import ExtractedField
    from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
    from app.features.extraction.schemas.field_status import FieldStatus

    stub = AsyncMock(spec=ExtractionService)
    stub.extract.return_value = ExtractionResult(
        response=ExtractResponse(
            skill_name="invoice",
            skill_version=1,
            fields={
                "number": ExtractedField(
                    name="number",
                    value="INV-1",
                    status=FieldStatus.extracted,
                    source="document",
                    grounded=True,
                    bbox_refs=[BoundingBoxRef(page=1, x0=0.0, y0=0.0, x1=1.0, y1=1.0)],
                ),
            },
            metadata=ExtractionMetadata(
                page_count=1,
                duration_ms=1,
                attempts_per_field={"number": 1},
            ),
        ),
        annotated_pdf_bytes=None,
    )
    app = _build_app(tmp_path, stub, max_pdf_bytes=1024 * 1024)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            data={
                "skill_name": "invoice",
                "skill_version": "1",
                "output_mode": "JSON_ONLY",
            },
            files={"pdf": ("test.pdf", b"%PDF-1.4 tiny", "application/pdf")},
        )

    assert response.status_code == 200
    stub.extract.assert_called_once()

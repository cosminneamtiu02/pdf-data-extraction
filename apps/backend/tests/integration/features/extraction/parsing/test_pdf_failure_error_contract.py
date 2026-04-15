"""Integration tests for PDFX-E003-F004 — PDF failure-mode error contract.

Verifies that the four PDF-side domain errors raised from
`DoclingDocumentParser.parse` serialize through the FastAPI exception handler
into the `ErrorResponse` envelope with the correct HTTP status codes and
machine-readable error code + params.

`/api/v1/extract` does not exist yet (PDFX-E006-F003). We mount an ad-hoc test
route that invokes a real `DoclingDocumentParser` configured with fake
dependencies, following the same pattern as
`tests/integration/test_skill_error_contract.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings

if TYPE_CHECKING:
    import pytest

if TYPE_CHECKING:
    from pathlib import Path
from app.exceptions import (
    PdfInvalidError,
    PdfNoTextExtractableError,
    PdfPasswordProtectedError,
)
from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
)
from app.main import create_app


@dataclass(frozen=True)
class _FakeItem:
    text: str
    page_number: int
    bbox_x0: float
    bbox_y0: float
    bbox_x1: float
    bbox_y1: float


@dataclass
class _FakeDoc:
    _page_count: int
    _items: tuple[_FakeItem, ...] = ()

    @property
    def page_count(self) -> int:
        return self._page_count

    def iter_text_items(self) -> list[_FakeItem]:
        return list(self._items)


def _factory(doc: _FakeDoc) -> Any:
    class _Conv:
        def convert(self, _pdf_bytes: bytes) -> _FakeDoc:
            return doc

    def _f(_config: DoclingConfig) -> _Conv:
        return _Conv()

    return _f


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


def _settings_with_skills(skills_dir: Path) -> Settings:
    return Settings(skills_dir=skills_dir, app_env="development")  # type: ignore[reportCallIssue]


def _make_client(tmp_path: Path) -> tuple[Any, ASGITransport]:
    _write_valid_skill(tmp_path)
    app = create_app(_settings_with_skills(tmp_path))
    return app, ASGITransport(app=app)


_CONFIG = DoclingConfig(ocr="auto", table_mode="fast")


async def test_pdf_invalid_returns_400_envelope(tmp_path: Path) -> None:
    app, transport = _make_client(tmp_path)

    def _rejecting_preflight(_pdf_bytes: bytes) -> int:
        raise PdfInvalidError

    parser = DoclingDocumentParser(
        converter_factory=_factory(_FakeDoc(_page_count=1)),
        pdf_preflight=_rejecting_preflight,
    )

    async def _route() -> None:
        await parser.parse(b"not a pdf", _CONFIG)

    app.add_api_route("/_test/pdf-invalid", _route, methods=["POST"])

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/_test/pdf-invalid")

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "PDF_INVALID"
    assert body["error"]["params"] == {}
    assert body["error"]["details"] is None
    assert "request_id" in body["error"]


async def test_pdf_password_protected_returns_400_envelope(tmp_path: Path) -> None:
    app, transport = _make_client(tmp_path)

    def _encrypted_preflight(_pdf_bytes: bytes) -> int:
        raise PdfPasswordProtectedError

    parser = DoclingDocumentParser(
        converter_factory=_factory(_FakeDoc(_page_count=1)),
        pdf_preflight=_encrypted_preflight,
    )

    async def _route() -> None:
        await parser.parse(b"%PDF-encrypted", _CONFIG)

    app.add_api_route("/_test/pdf-password", _route, methods=["POST"])

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/_test/pdf-password")

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "PDF_PASSWORD_PROTECTED"
    assert body["error"]["params"] == {}


async def test_pdf_too_many_pages_returns_413_envelope_with_params(tmp_path: Path) -> None:
    app, transport = _make_client(tmp_path)

    def _pf_201(_pdf_bytes: bytes) -> int:
        return 201

    parser = DoclingDocumentParser(
        converter_factory=_factory(_FakeDoc(_page_count=201)),
        pdf_preflight=_pf_201,
        max_pdf_pages=200,
    )

    async def _route() -> None:
        await parser.parse(b"%PDF-fake", _CONFIG)

    app.add_api_route("/_test/pdf-too-many-pages", _route, methods=["POST"])

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/_test/pdf-too-many-pages")

    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "PDF_TOO_MANY_PAGES"
    assert body["error"]["params"] == {"limit": 200, "actual": 201}


async def test_pdf_no_text_extractable_returns_422_envelope(tmp_path: Path) -> None:
    app, transport = _make_client(tmp_path)

    def _noop(_pdf_bytes: bytes) -> int:
        return 1

    parser = DoclingDocumentParser(
        converter_factory=_factory(_FakeDoc(_page_count=1, _items=())),
        pdf_preflight=_noop,
    )

    async def _route() -> None:
        await parser.parse(b"%PDF-fake", _CONFIG)

    app.add_api_route("/_test/pdf-empty", _route, methods=["POST"])

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/_test/pdf-empty")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "PDF_NO_TEXT_EXTRACTABLE"
    assert body["error"]["params"] == {}
    assert "request_id" in body["error"]


def test_pdf_error_classes_have_translation_message_shape() -> None:
    """Smoke check: each generated PdfError instantiates cleanly and produces a code."""
    assert PdfInvalidError().code == "PDF_INVALID"
    assert PdfPasswordProtectedError().code == "PDF_PASSWORD_PROTECTED"
    assert PdfNoTextExtractableError().code == "PDF_NO_TEXT_EXTRACTABLE"


def test_settings_reads_max_pdf_pages_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_PDF_PAGES", "50")
    s = Settings()  # type: ignore[reportCallIssue]
    assert s.max_pdf_pages == 50

"""Unit tests for FastAPI dependency factories in `app.api.deps`.

Focus: the factories that wire `Settings` into the objects that back the
API's `Depends()` tree.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.api import deps
from app.core.config import Settings
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
)


def _request(settings: Settings | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=settings or Settings()),  # type: ignore[reportCallIssue]
        ),
    )


def test_get_document_parser_returns_a_docling_document_parser() -> None:
    parser = deps.get_document_parser(_request())  # type: ignore[arg-type]
    assert isinstance(parser, DoclingDocumentParser)


def test_get_document_parser_is_cached_singleton_per_app() -> None:
    request = _request()

    first = deps.get_document_parser(request)  # type: ignore[arg-type]
    second = deps.get_document_parser(request)  # type: ignore[arg-type]

    assert first is second


def test_get_document_parser_reads_max_pdf_pages_from_app_state_settings() -> None:
    """Factory must read settings.max_pdf_pages — not the parser's own default.

    Regression guard: `MAX_PDF_PAGES` was exposed as a Settings field and
    documented in `.env.example`, but the runtime parser kept using its
    internal default of 200. Without this wiring, the env knob is a lie —
    operators who set MAX_PDF_PAGES=50 would still see 200-page PDFs
    accepted. This test proves the env variable controls the runtime cap.
    """
    settings = Settings(max_pdf_pages=17)  # type: ignore[reportCallIssue]

    parser = deps.get_document_parser(_request(settings))  # type: ignore[arg-type]

    assert parser._max_pdf_pages == 17  # noqa: SLF001 — factory wiring contract

"""Unit tests for FastAPI dependency factories in `app.api.deps`.

Focus: the factories that wire `Settings` into the objects that back the
API's `Depends()` tree. These tests clear the `lru_cache` on each factory
between assertions so env-derived overrides take effect immediately.
"""

from __future__ import annotations

import pytest

from app.api import deps
from app.features.extraction.parsing.docling_document_parser import (
    DoclingDocumentParser,
)


@pytest.fixture(autouse=True)
def _clear_deps_caches() -> None:
    deps.get_settings.cache_clear()
    # get_document_parser is added by PDFX-E003-F004's wiring fix. Guard the
    # cache_clear so RED-phase TDD runs don't AttributeError before the
    # factory exists.
    get_parser = getattr(deps, "get_document_parser", None)
    if get_parser is not None:
        get_parser.cache_clear()


def test_get_document_parser_returns_a_docling_document_parser() -> None:
    parser = deps.get_document_parser()
    assert isinstance(parser, DoclingDocumentParser)


def test_get_document_parser_is_cached_singleton() -> None:
    first = deps.get_document_parser()
    second = deps.get_document_parser()
    assert first is second


def test_get_document_parser_reads_max_pdf_pages_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory must read settings.max_pdf_pages — not the parser's own default.

    Regression guard: `MAX_PDF_PAGES` was exposed as a Settings field and
    documented in `.env.example`, but the runtime parser kept using its
    internal default of 200. Without this wiring, the env knob is a lie —
    operators who set MAX_PDF_PAGES=50 would still see 200-page PDFs
    accepted. This test proves the env variable controls the runtime cap.
    """
    monkeypatch.setenv("MAX_PDF_PAGES", "17")
    deps.get_settings.cache_clear()
    deps.get_document_parser.cache_clear()

    parser = deps.get_document_parser()

    assert parser._max_pdf_pages == 17  # noqa: SLF001 — factory wiring contract

"""Unit tests for extraction Depends factories (PDFX-E006-F002).

Uses the SimpleNamespace fake-request pattern from ``test_deps.py``.

Post issue #111: ``get_extraction_service`` now takes each pipeline
component as an ``Annotated[..., Depends(...)]`` parameter so FastAPI
resolves them through the overridable feature-level factories. When
called outside FastAPI's DI (i.e. from these unit tests), the components
must be supplied explicitly — the helper ``_resolve_components`` reuses
the real factories to keep the wiring invariants under test.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.api import deps as api_deps
from app.api.deps import get_extraction_service
from app.core.config import Settings
from app.features.extraction import deps as feature_deps
from app.features.extraction.service import ExtractionService
from app.features.extraction.skills.skill_manifest import SkillManifest


def _settings(tmp_path: Path) -> Settings:
    return Settings(skills_dir=tmp_path)  # type: ignore[reportCallIssue]


def _request(tmp_path: Path, *, settings: Settings | None = None) -> SimpleNamespace:
    resolved = settings or _settings(tmp_path)
    manifest = SkillManifest({})
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=resolved,
                skill_manifest=manifest,
            ),
        ),
    )


def _resolve_components(request: SimpleNamespace) -> dict[str, Any]:
    """Build the kwargs ``get_extraction_service`` expects via its real DI sources.

    In production FastAPI resolves these through the ``Depends()`` graph. In
    these unit tests we drive them directly, reusing the per-component
    factories so the wiring stays exercised.
    """
    return {
        "settings": api_deps.get_settings(request),  # type: ignore[arg-type]
        "skill_manifest": feature_deps.get_skill_manifest(request),  # type: ignore[arg-type]
        "document_parser": api_deps.get_document_parser(request),  # type: ignore[arg-type]
        "text_concatenator": feature_deps.get_text_concatenator(request),  # type: ignore[arg-type]
        "extraction_engine": feature_deps.get_extraction_engine(request),  # type: ignore[arg-type]
        "span_resolver": feature_deps.get_span_resolver(request),  # type: ignore[arg-type]
        "pdf_annotator": feature_deps.get_pdf_annotator(request),  # type: ignore[arg-type]
        "intelligence_provider": api_deps.get_intelligence_provider(request),  # type: ignore[arg-type]
    }


def test_get_extraction_service_returns_extraction_service(tmp_path: Path) -> None:
    request = _request(tmp_path)
    service = get_extraction_service(request, **_resolve_components(request))  # type: ignore[arg-type]
    assert isinstance(service, ExtractionService)


def test_get_extraction_service_is_cached_per_app(tmp_path: Path) -> None:
    request = _request(tmp_path)
    components = _resolve_components(request)
    first = get_extraction_service(request, **components)  # type: ignore[arg-type]
    second = get_extraction_service(request, **components)  # type: ignore[arg-type]
    assert first is second


def test_get_extraction_service_reads_extraction_timeout_from_settings(
    tmp_path: Path,
) -> None:
    settings = Settings(skills_dir=tmp_path, extraction_timeout_seconds=42.0)  # type: ignore[reportCallIssue]
    request = _request(tmp_path, settings=settings)
    service = get_extraction_service(request, **_resolve_components(request))  # type: ignore[arg-type]
    assert service._timeout_seconds == 42.0  # noqa: SLF001 — factory wiring contract

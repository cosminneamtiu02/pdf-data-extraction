"""Unit tests for extraction Depends factories (PDFX-E006-F002).

Uses the SimpleNamespace fake-request pattern from ``test_deps.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.core.config import Settings
from app.features.extraction.deps import get_extraction_service
from app.features.extraction.service import ExtractionService
from app.features.extraction.skills.skill_manifest import SkillManifest


def _settings(tmp_path: Path) -> Settings:
    return Settings(skills_dir=tmp_path)  # type: ignore[reportCallIssue]


def _request(tmp_path: Path) -> SimpleNamespace:
    settings = _settings(tmp_path)
    manifest = SkillManifest({})
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                skill_manifest=manifest,
            ),
        ),
    )


def test_get_extraction_service_returns_extraction_service(tmp_path: Path) -> None:
    request = _request(tmp_path)
    service = get_extraction_service(request)  # type: ignore[arg-type]
    assert isinstance(service, ExtractionService)


def test_get_extraction_service_is_cached_per_app(tmp_path: Path) -> None:
    request = _request(tmp_path)
    first = get_extraction_service(request)  # type: ignore[arg-type]
    second = get_extraction_service(request)  # type: ignore[arg-type]
    assert first is second


def test_get_extraction_service_reads_extraction_timeout_from_settings(
    tmp_path: Path,
) -> None:
    settings = Settings(skills_dir=tmp_path, extraction_timeout_seconds=42.0)  # type: ignore[reportCallIssue]
    manifest = SkillManifest({})
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=settings, skill_manifest=manifest),
        ),
    )
    service = get_extraction_service(request)  # type: ignore[arg-type]
    assert service._timeout_seconds == 42.0  # noqa: SLF001 — factory wiring contract

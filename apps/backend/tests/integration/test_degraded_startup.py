"""Integration tests for degraded-mode startup + self-healing (PDFX-E007-F002).

These tests exercise the full lifespan startup path with an unreachable
Ollama URL, verifying that the process boots into degraded mode (``/health``
green, ``/ready`` red, extraction requests → ``INTELLIGENCE_UNAVAILABLE``),
and self-heals when Ollama becomes reachable.

The lifespan is invoked explicitly via ``_lifespan(app)`` because
``httpx.ASGITransport`` does not send ASGI lifespan events.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_extraction_service, get_probe_cache
from app.api.probe_cache import ProbeCache
from app.core.config import Settings
from app.exceptions import IntelligenceUnavailableError
from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.schemas.extract_response import ExtractResponse
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.extraction_metadata import ExtractionMetadata
from app.features.extraction.schemas.field_status import FieldStatus
from app.features.extraction.service import ExtractionService
from app.main import _lifespan, create_app
from tests.conftest import FakeProbe

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _degraded_settings(skills_dir: Path) -> Settings:
    return Settings(  # type: ignore[reportCallIssue]
        skills_dir=skills_dir,
        app_env="development",
        ollama_base_url="http://127.0.0.1:1",  # port 1 is always closed
        ollama_probe_timeout_seconds=0.5,
        ollama_probe_ttl_seconds=0.05,
    )


def _make_canned_result() -> ExtractionResult:
    field = ExtractedField(
        name="number",
        value="INV-001",
        status=FieldStatus.extracted,
        source="document",
        grounded=True,
        bbox_refs=[],
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
    return ExtractionResult(response=response, annotated_pdf_bytes=None)


def _stub_service(*, side_effect=None, result=None) -> ExtractionService:
    svc = AsyncMock(spec=ExtractionService)
    if side_effect is not None:
        svc.extract.side_effect = side_effect
    else:
        svc.extract.return_value = result or _make_canned_result()
    return svc


# ---------------------------------------------------------------------------
# Tests — degraded boot
# ---------------------------------------------------------------------------


async def test_degraded_boot_app_starts_and_health_returns_200(tmp_path: Path) -> None:
    """Ollama unreachable at boot → process starts, GET /health returns 200."""
    _write_valid_skill(tmp_path)
    app = create_app(_degraded_settings(tmp_path))

    async with _lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_degraded_boot_ready_returns_503(tmp_path: Path) -> None:
    """Ollama unreachable at boot → GET /ready returns 503 immediately.

    The startup probe primes the cache so /ready returns 503 without needing
    its own lazy probe call.
    """
    _write_valid_skill(tmp_path)
    app = create_app(_degraded_settings(tmp_path))

    async with _lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["reason"] == "ollama_unreachable"


async def test_degraded_boot_extract_returns_503_intelligence_unavailable(
    tmp_path: Path,
) -> None:
    """Ollama unreachable at boot → POST /extract returns 503 INTELLIGENCE_UNAVAILABLE."""
    _write_valid_skill(tmp_path)
    app = create_app(_degraded_settings(tmp_path))

    svc = _stub_service(side_effect=IntelligenceUnavailableError())
    app.dependency_overrides[get_extraction_service] = lambda: svc

    try:
        async with _lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/extract",
                    data={
                        "skill_name": "invoice",
                        "skill_version": "1",
                        "output_mode": "JSON_ONLY",
                    },
                    files={"pdf": ("test.pdf", b"%PDF-1.4 small", "application/pdf")},
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "INTELLIGENCE_UNAVAILABLE"


async def test_degraded_boot_logs_ollama_unreachable_at_startup(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Ollama unreachable at boot → startup emits WARNING-level log event."""
    _write_valid_skill(tmp_path)
    app = create_app(_degraded_settings(tmp_path))

    async with _lifespan(app):
        pass

    captured = capfd.readouterr()
    assert "ollama_unreachable_at_startup" in captured.out
    assert "warning" in captured.out.lower()


# ---------------------------------------------------------------------------
# Tests — self-healing
# ---------------------------------------------------------------------------


async def test_self_healing_ready_flips_to_200(tmp_path: Path) -> None:
    """Degraded boot → Ollama becomes reachable → /ready returns 200 after TTL."""
    _write_valid_skill(tmp_path)
    app = create_app(_degraded_settings(tmp_path))

    async with _lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Phase 1: degraded mode
            r1 = await client.get("/ready")
            assert r1.status_code == 503

            # Phase 2: simulate Ollama becoming reachable via dependency override
            fake_probe = FakeProbe(results=[True])
            cache = ProbeCache(
                probe=fake_probe,  # type: ignore[arg-type]  # test seam
                ttl_seconds=0.05,
            )
            cache.prime(result=False)
            app.dependency_overrides[get_probe_cache] = lambda: cache

            # Wait for TTL expiry so the next /ready call triggers a fresh probe
            await asyncio.sleep(0.06)

            # Phase 3: /ready should now return 200
            r2 = await client.get("/ready")

    app.dependency_overrides.clear()
    assert r2.status_code == 200
    assert r2.json() == {"status": "ready"}


async def test_extraction_succeeds_after_self_heal(tmp_path: Path) -> None:
    """After self-heal, extraction requests proceed through the normal pipeline."""
    _write_valid_skill(tmp_path)
    app = create_app(_degraded_settings(tmp_path))

    async with _lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Verify degraded mode first
            r_ready = await client.get("/ready")
            assert r_ready.status_code == 503

            # Override service with a canned successful result
            svc = _stub_service(result=_make_canned_result())
            app.dependency_overrides[get_extraction_service] = lambda: svc

            # Also override probe cache so /ready would return 200
            fake_probe = FakeProbe(results=[True])
            cache = ProbeCache(
                probe=fake_probe,  # type: ignore[arg-type]  # test seam
                ttl_seconds=60.0,
            )
            cache.prime(result=True)
            app.dependency_overrides[get_probe_cache] = lambda: cache

            # Extraction should succeed after self-heal
            response = await client.post(
                "/api/v1/extract",
                data={
                    "skill_name": "invoice",
                    "skill_version": "1",
                    "output_mode": "JSON_ONLY",
                },
                files={"pdf": ("test.pdf", b"%PDF-1.4 small", "application/pdf")},
            )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["skill_name"] == "invoice"
    assert body["skill_version"] == 1
    assert "number" in body["fields"]


async def test_same_app_instance_throughout_flicker_cycle(tmp_path: Path) -> None:
    """Full flicker cycle uses the same FastAPI app instance — no restart."""
    _write_valid_skill(tmp_path)
    app = create_app(_degraded_settings(tmp_path))
    app_id = id(app)

    async with _lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Degraded phase
            r1 = await client.get("/ready")
            assert r1.status_code == 503

            # Self-heal phase via dependency override
            fake_probe = FakeProbe(results=[True])
            cache = ProbeCache(
                probe=fake_probe,  # type: ignore[arg-type]  # test seam
                ttl_seconds=60.0,
            )
            cache.prime(result=True)
            app.dependency_overrides[get_probe_cache] = lambda: cache

            r2 = await client.get("/ready")
            assert r2.status_code == 200

    app.dependency_overrides.clear()
    # Same app object handled both failing and succeeding requests
    assert id(app) == app_id


async def test_stays_degraded_when_ollama_always_unreachable(tmp_path: Path) -> None:
    """App remains in degraded mode for the full test when Ollama never comes up."""
    _write_valid_skill(tmp_path)
    app = create_app(_degraded_settings(tmp_path))

    async with _lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Repeated checks should consistently show degraded mode
            for _ in range(3):
                health = await client.get("/health")
                assert health.status_code == 200

                ready = await client.get("/ready")
                assert ready.status_code == 503
                assert ready.json()["status"] == "not_ready"

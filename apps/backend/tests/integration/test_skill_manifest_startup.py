"""Integration tests for skill manifest wiring into FastAPI startup."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_probe_cache
from app.api.probe_cache import ProbeCache
from app.core.config import Settings
from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills import SkillManifest
from app.main import create_app


def _write_skill(base: Path, *, dir_name: str, **overrides: Any) -> Path:
    file_name = overrides.pop("file_name", "1.yaml")
    name = overrides.pop("name", dir_name)
    version = overrides.pop("version", 1)
    docling = overrides.pop("docling", None)
    body: dict[str, Any] = {
        "name": name or dir_name,
        "version": version,
        "prompt": "Extract header fields.",
        "examples": [{"input": "INV-1", "output": {"number": "INV-1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
    }
    if docling is not None:
        body["docling"] = docling
    target = base / dir_name
    target.mkdir(parents=True, exist_ok=True)
    path = target / file_name
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def _settings_with_skills(skills_dir: Path) -> Settings:
    # pydantic-settings loads the remaining fields from env / defaults
    return Settings(skills_dir=skills_dir)  # type: ignore[reportCallIssue]


async def test_create_app_populates_manifest_on_state(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    _write_skill(tmp_path, dir_name="invoice", file_name="2.yaml", version=2)

    app = create_app(_settings_with_skills(tmp_path))

    manifest = app.state.skill_manifest
    assert isinstance(manifest, SkillManifest)
    assert manifest.lookup("invoice", "latest").version == 2


async def test_ready_still_returns_200_after_manifest_wiring(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    app = create_app(_settings_with_skills(tmp_path))

    # /ready is now gated on an Ollama probe (PDFX-E007-F001).  Override the
    # probe-cache dependency so this test stays isolated from real Ollama.
    class _AlwaysReady:
        async def check(self) -> bool:
            return True

    cache = ProbeCache(probe=_AlwaysReady(), ttl_seconds=60.0)  # type: ignore[arg-type]  # test seam
    app.dependency_overrides[get_probe_cache] = lambda: cache

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    app.dependency_overrides.clear()
    assert response.status_code == 200


async def test_create_app_fails_fast_on_malformed_yaml(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="2.yaml", version=1)  # mismatch

    with pytest.raises(SkillValidationFailedError) as exc_info:
        create_app(_settings_with_skills(tmp_path))

    assert exc_info.value.params is not None
    reason = exc_info.value.params.model_dump()["reason"]
    assert "2.yaml" in reason


async def test_create_app_fails_fast_on_missing_skills_dir(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere"

    with pytest.raises(SkillValidationFailedError) as exc_info:
        create_app(_settings_with_skills(missing))

    assert exc_info.value.params is not None
    assert "nowhere" in exc_info.value.params.model_dump()["reason"]


async def test_manifest_is_stable_across_reads(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    app = create_app(_settings_with_skills(tmp_path))

    first = app.state.skill_manifest
    second = app.state.skill_manifest

    assert first is second


async def test_docling_override_flows_from_settings(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        dir_name="invoice",
        file_name="1.yaml",
        version=1,
        docling={"ocr": "off"},
    )

    # pydantic-settings loads the remaining fields from env / defaults
    settings = Settings(  # type: ignore[reportCallIssue]
        skills_dir=tmp_path,
        docling_ocr_default="auto",
        docling_table_mode_default="fast",
    )
    app = create_app(settings)

    skill = app.state.skill_manifest.lookup("invoice", "latest")
    assert skill.docling_config.ocr == "off"  # per-skill override wins
    assert skill.docling_config.table_mode == "fast"  # default fills gap

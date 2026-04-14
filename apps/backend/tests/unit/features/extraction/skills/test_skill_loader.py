"""Unit tests for `SkillLoader` — filesystem walk + aggregated validation."""

import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from structlog.testing import capture_logs

from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills import SkillDoclingConfig
from app.features.extraction.skills.skill_loader import SkillLoader


def _reason(err: SkillValidationFailedError) -> str:
    assert err.params is not None
    dumped = err.params.model_dump()
    value = dumped["reason"]
    assert isinstance(value, str)
    return value


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

    target_dir = base / dir_name
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / file_name
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def test_load_three_valid_skills_returns_keyed_dict(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    _write_skill(tmp_path, dir_name="invoice", file_name="2.yaml", version=2)
    _write_skill(tmp_path, dir_name="research_paper", file_name="1.yaml", version=1)

    loaded = SkillLoader().load(tmp_path)

    assert set(loaded.keys()) == {
        ("invoice", 1),
        ("invoice", 2),
        ("research_paper", 1),
    }
    assert loaded[("invoice", 2)].version == 2
    assert loaded[("research_paper", 1)].name == "research_paper"


def test_load_empty_directory_returns_empty_dict_and_warns(tmp_path: Path) -> None:
    # `capture_logs` hooks into structlog's processor chain directly, so the
    # assertion works regardless of whether `configure_logging()` has been
    # called yet in the test session — unlike `caplog`, which only sees events
    # once structlog is routed through stdlib.
    with capture_logs() as logs:
        loaded = SkillLoader().load(tmp_path)

    assert loaded == {}
    assert any(entry.get("event") == "skill_manifest_empty" for entry in logs)


def test_load_missing_directory_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope"

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(missing)

    assert "nope" in _reason(exc_info.value)


def test_load_ignores_top_level_yaml(tmp_path: Path) -> None:
    (tmp_path / "stray.yaml").write_text("name: stray\nversion: 1\n", encoding="utf-8")

    loaded = SkillLoader().load(tmp_path)

    assert loaded == {}


def test_load_filename_version_mismatch_raises(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="2.yaml", version=1)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    reason = _reason(exc_info.value)
    assert "2.yaml" in reason
    assert "filename version" in reason


def test_load_aggregates_multiple_broken_files(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "1.yaml").write_text(": : not yaml :", encoding="utf-8")
    _write_skill(tmp_path, dir_name="mismatch", file_name="2.yaml", version=1)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    reason = _reason(exc_info.value)
    assert "broken/1.yaml" in reason or "broken" in reason
    assert "mismatch/2.yaml" in reason or "mismatch" in reason


def test_load_directory_name_mismatch_raises(tmp_path: Path) -> None:
    """`invoice/1.yaml` with body `name: receipt` must not silently become `(receipt, 1)`."""
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", name="receipt", version=1)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    reason = _reason(exc_info.value)
    assert "directory name 'invoice'" in reason
    assert "body name 'receipt'" in reason


def test_load_applies_docling_defaults(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)

    loader = SkillLoader(
        default_docling=SkillDoclingConfig(ocr="on", table_mode="fast"),
    )
    loaded = loader.load(tmp_path)

    assert loaded[("invoice", 1)].docling_config.ocr == "on"
    assert loaded[("invoice", 1)].docling_config.table_mode == "fast"


def test_load_skill_override_beats_default(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        dir_name="invoice",
        file_name="1.yaml",
        version=1,
        docling={"ocr": "off"},
    )

    loader = SkillLoader(
        default_docling=SkillDoclingConfig(ocr="on", table_mode="fast"),
    )
    loaded = loader.load(tmp_path)

    skill = loaded[("invoice", 1)]
    assert skill.docling_config.ocr == "off"  # override wins
    assert skill.docling_config.table_mode == "fast"  # default fills gap


@pytest.mark.slow
def test_load_100_skills_under_two_seconds(tmp_path: Path) -> None:
    for idx in range(100):
        _write_skill(
            tmp_path,
            dir_name=f"skill_{idx:03d}",
            file_name="1.yaml",
            name=f"skill_{idx:03d}",
            version=1,
        )

    start = time.perf_counter()
    loaded = SkillLoader().load(tmp_path)
    elapsed = time.perf_counter() - start

    assert len(loaded) == 100
    assert elapsed < 2.0

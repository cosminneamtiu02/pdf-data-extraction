"""Unit tests for `SkillManifest` — keyed lookup, `latest` resolution, misses."""

from typing import Any

import pytest

from app.exceptions import SkillNotFoundError
from app.features.extraction.skills import Skill, SkillDoclingConfig, SkillExample
from app.features.extraction.skills.skill_manifest import SkillManifest


def _make_skill(name: str, version: int) -> Skill:
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"number": {"type": "string"}},
        "required": ["number"],
    }
    return Skill(
        name=name,
        version=version,
        description=None,
        prompt="Extract header fields.",
        examples=(SkillExample(input="INV-1", output={"number": "INV-1"}),),
        output_schema=output_schema,
        docling_config=SkillDoclingConfig(),
    )


@pytest.fixture
def manifest() -> SkillManifest:
    return SkillManifest(
        {
            ("invoice", 1): _make_skill("invoice", 1),
            ("invoice", 2): _make_skill("invoice", 2),
            ("research_paper", 1): _make_skill("research_paper", 1),
        },
    )


def test_lookup_latest_resolves_to_highest_version(manifest: SkillManifest) -> None:
    assert manifest.lookup("invoice", "latest").version == 2


def test_lookup_integer_version_returns_that_skill(manifest: SkillManifest) -> None:
    assert manifest.lookup("invoice", "1").version == 1


def test_lookup_single_version_latest(manifest: SkillManifest) -> None:
    assert manifest.lookup("research_paper", "latest").version == 1


def test_lookup_unknown_name_raises(manifest: SkillManifest) -> None:
    with pytest.raises(SkillNotFoundError) as exc_info:
        manifest.lookup("mystery", "1")

    assert exc_info.value.params is not None
    dumped = exc_info.value.params.model_dump()
    assert dumped["name"] == "mystery"
    assert dumped["version"] == "1"


def test_lookup_unknown_version_raises(manifest: SkillManifest) -> None:
    with pytest.raises(SkillNotFoundError) as exc_info:
        manifest.lookup("invoice", "99")

    assert exc_info.value.params is not None
    assert exc_info.value.params.model_dump()["version"] == "99"


def test_lookup_non_integer_version_raises(manifest: SkillManifest) -> None:
    with pytest.raises(SkillNotFoundError):
        manifest.lookup("invoice", "not-a-version")


def test_lookup_is_idempotent(manifest: SkillManifest) -> None:
    first = manifest.lookup("invoice", "latest")
    second = manifest.lookup("invoice", "latest")
    assert first is second


def test_empty_manifest_lookup_raises() -> None:
    manifest = SkillManifest({})

    with pytest.raises(SkillNotFoundError):
        manifest.lookup("anything", "latest")

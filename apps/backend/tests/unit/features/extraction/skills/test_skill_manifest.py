"""Unit tests for `SkillManifest` — keyed lookup, `latest` resolution, misses."""

import pytest

from app.exceptions import SkillNotFoundError
from app.features.extraction.skills.skill_manifest import SkillManifest
from tests._support.skill_factory import make_skill


@pytest.fixture
def manifest() -> SkillManifest:
    return SkillManifest(
        {
            ("invoice", 1): make_skill("invoice", 1),
            ("invoice", 2): make_skill("invoice", 2),
            ("research_paper", 1): make_skill("research_paper", 1),
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


def test_empty_manifest_is_empty_true() -> None:
    assert SkillManifest({}).is_empty is True


def test_populated_manifest_is_empty_false(manifest: SkillManifest) -> None:
    assert manifest.is_empty is False


def test_make_skill_helper_produces_valid_skill() -> None:
    """Guard against silent ``make_skill`` breakage across test modules.

    ``make_skill`` lives in ``tests/_support/skill_factory.py`` and is
    imported by unit and integration tests; if its shape drifts from
    ``Skill``'s runtime invariants, the callers surface misleading
    errors. This test is the canonical check so breakage shows up here
    first.
    """
    skill = make_skill("invoice", 7)
    assert skill.name == "invoice"
    assert skill.version == 7

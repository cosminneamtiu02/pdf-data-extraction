"""Factory for building minimal valid ``Skill`` objects in tests.

Moved out of ``tests/conftest.py`` (issue #354) so that unit tests which
never touch the skills feature do not pay the extraction-skills import
tax at conftest load time. Callers that need a skill instance import
``make_skill`` directly from this module.
"""

from __future__ import annotations

from typing import Any

from app.features.extraction.skills import Skill, SkillDoclingConfig, SkillExample
from app.features.extraction.skills.deep_freeze import deep_freeze_mapping


def make_skill(name: str, version: int) -> Skill:
    """Construct a minimal valid ``Skill`` for test fixtures.

    Lives in ``tests/_support/`` (not under any specific test package)
    because multiple test modules across unit and integration need it --
    keeping it co-located with ``test_skill_manifest.py`` forced cross-test
    imports that coupled unrelated files to that module's private helper.
    """
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"number": {"type": "string"}},
        "required": ["number"],
    }
    # Deep-freeze to match production `Skill.from_schema` behaviour so
    # accidental in-test mutations fail the same way they would at runtime.
    return Skill(
        name=name,
        version=version,
        description=None,
        prompt="Extract header fields.",
        examples=(SkillExample(input="INV-1", output={"number": "INV-1"}),),
        output_schema=deep_freeze_mapping(output_schema),
        docling_config=SkillDoclingConfig(),
    )

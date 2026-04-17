"""Shared test fixtures and fakes used across unit and integration tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.features.extraction.skills import Skill, SkillDoclingConfig, SkillExample


class FakeProbe:
    """Controllable probe returning scripted boolean results.

    Used by both unit tests (``test_probe_cache``) and integration tests
    (``test_health``) to stub ``OllamaHealthProbe.check()`` without a
    real Ollama instance.
    """

    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.call_count = 0

    async def check(self) -> bool:
        if self.call_count >= len(self._results):
            pytest.fail(
                f"FakeProbe.check called more times than scripted (call #{self.call_count + 1})"
            )
        result = self._results[self.call_count]
        self.call_count += 1
        return result


def make_skill(name: str, version: int) -> Skill:
    """Construct a minimal valid ``Skill`` for test fixtures.

    Lives here (not in ``tests/unit/features/extraction/skills/...``) because
    multiple test modules across unit and integration need it — keeping it
    co-located with ``test_skill_manifest.py`` forced cross-test imports
    that coupled unrelated files to that module's private helper.
    """
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

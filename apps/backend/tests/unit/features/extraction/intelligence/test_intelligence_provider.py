"""Unit tests for IntelligenceProvider Protocol."""

from typing import Any

from app.features.extraction.intelligence.generation_result import GenerationResult
from app.features.extraction.intelligence.intelligence_provider import IntelligenceProvider


class _SatisfyingProvider:
    async def generate(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> GenerationResult:
        _ = prompt
        _ = output_schema
        return GenerationResult(data={"ok": True}, attempts=1, raw_output='{"ok": true}')


class _NonConformingProvider:
    def something_else(self) -> None:
        pass


def test_satisfying_class_passes_runtime_check() -> None:
    provider = _SatisfyingProvider()

    assert isinstance(provider, IntelligenceProvider)


def test_non_conforming_class_fails_runtime_check() -> None:
    provider = _NonConformingProvider()

    assert not isinstance(provider, IntelligenceProvider)

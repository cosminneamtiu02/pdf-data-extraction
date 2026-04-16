"""Shared test fixtures and fakes used across unit and integration tests."""

from __future__ import annotations

import pytest


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

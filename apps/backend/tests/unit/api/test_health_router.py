"""Unit tests for health_router handler logic.

These test the handler return values directly, without the HTTP stack.
Integration tests (in tests/integration/test_health.py) cover the full
ASGI round-trip.
"""

from __future__ import annotations

import json

from app.api.health_router import health, ready
from app.api.schemas.not_ready_response import NotReadyResponse
from app.api.schemas.ready_response import ReadyResponse
from app.features.extraction.skills import SkillManifest
from tests.conftest import make_skill

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProbeCache:
    """Minimal ProbeCache stub returning a fixed ``is_ready`` result."""

    def __init__(self, *, ready: bool) -> None:
        self._ready = ready

    async def is_ready(self) -> bool:
        return self._ready


def _non_empty_manifest() -> SkillManifest:
    return SkillManifest({("invoice", 1): make_skill("invoice", 1)})


def _empty_manifest() -> SkillManifest:
    return SkillManifest({})


# ---------------------------------------------------------------------------
# /health tests
# ---------------------------------------------------------------------------


async def test_health_returns_ok() -> None:
    result = await health()
    assert result == {"status": "ok"}


# ---------------------------------------------------------------------------
# /ready tests
# ---------------------------------------------------------------------------


async def test_ready_returns_200_when_probe_cache_ready() -> None:
    cache = _FakeProbeCache(ready=True)
    response = await ready(
        probe_cache=cache,  # type: ignore[arg-type]  # test seam
        skill_manifest=_non_empty_manifest(),
    )
    assert response.status_code == 200
    assert response.body == b'{"status":"ready"}'


async def test_ready_returns_503_when_probe_cache_not_ready() -> None:
    cache = _FakeProbeCache(ready=False)
    response = await ready(
        probe_cache=cache,  # type: ignore[arg-type]  # test seam
        skill_manifest=_non_empty_manifest(),
    )
    assert response.status_code == 503
    assert b'"not_ready"' in response.body
    assert b'"ollama_unreachable"' in response.body


async def test_ready_returns_503_when_skill_manifest_empty() -> None:
    """Empty manifest → 503 with no_skills_loaded, even if Ollama is reachable.

    The skill-manifest check runs before the probe-cache check because
    skills are static operator config: if the manifest is empty, no
    amount of Ollama reachability lets the service answer requests.
    """
    cache = _FakeProbeCache(ready=True)
    response = await ready(
        probe_cache=cache,  # type: ignore[arg-type]  # test seam
        skill_manifest=_empty_manifest(),
    )
    assert response.status_code == 503
    assert b'"not_ready"' in response.body
    assert b'"no_skills_loaded"' in response.body


async def test_ready_prefers_no_skills_loaded_over_ollama_unreachable() -> None:
    """When both conditions are true, no_skills_loaded wins.

    Returning ``ollama_unreachable`` while the skills dir is also empty
    would hide the operator-config problem behind a runtime-health
    problem, sending operators to debug the wrong layer.
    """
    cache = _FakeProbeCache(ready=False)
    response = await ready(
        probe_cache=cache,  # type: ignore[arg-type]  # test seam
        skill_manifest=_empty_manifest(),
    )
    assert response.status_code == 503
    assert b'"no_skills_loaded"' in response.body
    assert b'"ollama_unreachable"' not in response.body


# ---------------------------------------------------------------------------
# Schema-parity tests (issue #374)
#
# Guard against the handler hand-constructing a dict that drifts from the
# Pydantic schema declared in ``app/api/schemas/``. The handler must build
# its JSON body from ``ReadyResponse(...).model_dump()`` /
# ``NotReadyResponse(...).model_dump()`` so any field added to the schema
# is guaranteed to appear in the runtime body without a follow-up handler
# edit. These tests iterate ``model_fields`` rather than hard-coding field
# names so future additions are covered automatically.
# ---------------------------------------------------------------------------


async def test_ready_response_body_contains_all_schema_fields_when_ready() -> None:
    """The 200 body must include every field declared by ``ReadyResponse``."""
    cache = _FakeProbeCache(ready=True)
    response = await ready(
        probe_cache=cache,  # type: ignore[arg-type]  # test seam
        skill_manifest=_non_empty_manifest(),
    )
    body = json.loads(response.body)
    for field_name in ReadyResponse.model_fields:
        assert field_name in body, (
            f"/ready 200 body is missing schema field '{field_name}'; "
            f"the handler must build its payload from "
            f"ReadyResponse(...).model_dump() to stay in sync with the schema."
        )
    assert body == ReadyResponse(status="ready").model_dump()


async def test_not_ready_response_body_contains_all_schema_fields_when_probe_fails() -> None:
    """The 503 body must include every field declared by ``NotReadyResponse``."""
    cache = _FakeProbeCache(ready=False)
    response = await ready(
        probe_cache=cache,  # type: ignore[arg-type]  # test seam
        skill_manifest=_non_empty_manifest(),
    )
    body = json.loads(response.body)
    for field_name in NotReadyResponse.model_fields:
        assert field_name in body, (
            f"/ready 503 body is missing schema field '{field_name}'; "
            f"the handler must build its payload from "
            f"NotReadyResponse(...).model_dump() to stay in sync with the schema."
        )
    assert (
        body
        == NotReadyResponse(
            status="not_ready",
            reason="ollama_unreachable",
        ).model_dump()
    )

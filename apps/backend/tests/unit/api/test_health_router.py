"""Unit tests for health_router handler logic.

These test the handler return values directly, without the HTTP stack.
Integration tests (in tests/integration/test_health.py) cover the full
ASGI round-trip.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import BaseModel, Field

from app.api import health_router
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
    # Compare parsed body to the model's JSON-mode dump rather than an exact
    # byte string — an exact-byte assertion would regress the moment
    # ``ReadyResponse`` gains any field (issue #374 follow-up: Copilot thread C
    # on PR #496).
    assert json.loads(response.body) == ReadyResponse(status="ready").model_dump(
        mode="json",
    )


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
# its JSON body from ``ReadyResponse(...).model_dump(mode="json")`` /
# ``NotReadyResponse(...).model_dump(mode="json")`` so any field added to the
# schema is guaranteed to appear in the runtime body without a follow-up
# handler edit, and so non-JSON-native types (datetime, UUID, Decimal, enums)
# remain serializable by Starlette's stdlib JSON encoder. These tests iterate
# ``model_fields`` rather than hard-coding field names so future additions are
# covered automatically.
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
            f"ReadyResponse(...).model_dump(mode='json') to stay in sync with the schema."
        )
    assert body == ReadyResponse(status="ready").model_dump(mode="json")


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
            f"NotReadyResponse(...).model_dump(mode='json') to stay in sync with the schema."
        )
    assert body == NotReadyResponse(
        status="not_ready",
        reason="ollama_unreachable",
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Regression guard for mode="json" (Copilot review threads A/B/D/E/F/G/H on PR #496)
#
# ``BaseModel.model_dump()`` defaults to ``mode="python"``, which returns
# ``datetime``/``UUID``/``Decimal``/enum values as their Python types.
# ``JSONResponse`` serializes with the stdlib ``json`` encoder, which raises
# ``TypeError`` on those types — the 200 handler would regress to a 500 the
# moment ``ReadyResponse`` gained such a field. Switching to
# ``mode="json"`` produces ISO-8601 strings, hex UUIDs, plain strings for
# Decimals, and primitive values for enums — all directly JSON-serializable.
#
# This test monkeypatches the ``ReadyResponse`` symbol the handler resolves at
# call time with a subclass that carries a ``datetime`` field defaulting to a
# fixed instant.  Without ``mode="json"`` the handler raises ``TypeError``;
# with ``mode="json"`` the datetime is serialized as an ISO-8601 string and
# the response renders successfully.
# ---------------------------------------------------------------------------


class _ReadyResponseWithTimestamp(BaseModel):
    """Stand-in for a future ``ReadyResponse`` carrying a non-JSON-native field.

    Used only by the monkeypatched regression test below.  Declares its own
    ``status`` field (rather than subclassing ``ReadyResponse``) so the field
    ordering in the serialized body is stable and predictable.
    """

    status: Literal["ready"]
    # ``default_factory`` returns a fresh ``datetime`` on each construction —
    # identical fixed values across test runs would let an accidental bytes-eq
    # assertion sneak past the guard.
    generated_at: datetime = Field(
        default_factory=lambda: datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC),
    )


async def test_ready_200_handles_non_json_native_schema_fields_without_typeerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handler must use ``mode='json'`` so ``JSONResponse`` never raises ``TypeError``.

    Regression guard for PR #496 Copilot threads D/E/F/G — without
    ``mode='json'`` in the handler, this test fails with
    ``TypeError: Object of type datetime is not JSON serializable``.
    """
    monkeypatch.setattr(health_router, "ReadyResponse", _ReadyResponseWithTimestamp)
    cache = _FakeProbeCache(ready=True)

    response = await ready(
        probe_cache=cache,  # type: ignore[arg-type]  # test seam
        skill_manifest=_non_empty_manifest(),
    )

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["status"] == "ready"
    # The datetime must be serialized as an ISO-8601 string, not a Python
    # ``datetime`` repr — ``mode='json'`` is what delivers this.
    assert body["generated_at"] == "2026-04-22T12:00:00Z"

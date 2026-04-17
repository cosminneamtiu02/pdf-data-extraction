"""Health and readiness endpoints — mounted at root, outside /api/v1/."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import get_probe_cache, get_skill_manifest
from app.api.probe_cache import (
    ProbeCache,  # runtime: FastAPI resolves Annotated[..., Depends()]
)
from app.features.extraction.skills import SkillManifest

router = APIRouter(tags=["health"])


class _ReadyResponse(BaseModel):
    status: Literal["ready"]


class _NotReadyResponse(BaseModel):
    status: Literal["not_ready"]
    # ``no_skills_loaded`` covers the production-container scenario where the
    # image ships ``apps/backend/skills/`` holding only ``.gitkeep`` and the
    # operator has not mounted a real skills directory over it. Without this
    # dimension, ``/ready`` would report green and every extraction request
    # would 404 on skill lookup (issue #108).
    reason: Literal["ollama_unreachable", "no_skills_loaded"]


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is alive."""
    return {"status": "ok"}


@router.get(
    "/ready",
    responses={
        200: {
            "description": ("Ollama is reachable and the skill manifest is populated."),
            "model": _ReadyResponse,
        },
        503: {
            "description": ("Ollama is unreachable or no skills are loaded; service is not ready."),
            "model": _NotReadyResponse,
        },
    },
)
async def ready(
    probe_cache: Annotated[ProbeCache, Depends(get_probe_cache)],
    skill_manifest: Annotated[SkillManifest, Depends(get_skill_manifest)],
) -> JSONResponse:
    """Readiness probe gated on Ollama reachability AND skill availability.

    The skill-manifest check runs first because it is static operator
    config: an empty manifest cannot be healed by Ollama coming up, so
    surfacing that dimension before the Ollama dimension sends
    operators to the right layer to debug.
    """
    if skill_manifest.is_empty:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "no_skills_loaded"},
        )
    if await probe_cache.is_ready():
        return JSONResponse(status_code=200, content={"status": "ready"})
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "reason": "ollama_unreachable"},
    )

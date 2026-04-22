"""Health and readiness endpoints — mounted at root, outside /api/v1/."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import get_probe_cache, get_skill_manifest
from app.api.probe_cache import (
    ProbeCache,  # runtime: FastAPI resolves Annotated[..., Depends()]
)
from app.api.schemas.not_ready_response import NotReadyResponse
from app.api.schemas.ready_response import ReadyResponse
from app.features.extraction.skills import SkillManifest

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is alive."""
    return {"status": "ok"}


@router.get(
    "/ready",
    responses={
        200: {
            "description": ("Ollama is reachable and the skill manifest is populated."),
            "model": ReadyResponse,
        },
        503: {
            "description": ("Ollama is unreachable or no skills are loaded; service is not ready."),
            "model": NotReadyResponse,
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

    Every ``model_dump`` call uses ``mode="json"`` so the dict handed to
    ``JSONResponse`` is guaranteed JSON-serializable.  The default
    ``mode="python"`` preserves Python types (``datetime``, ``UUID``,
    enums, ``Decimal``) that Starlette's stdlib JSON encoder cannot
    serialize, which would surface as a 500 the moment either response
    schema gains such a field — see the parity tests below for the
    monkeypatched regression guard.
    """
    if skill_manifest.is_empty:
        return JSONResponse(
            status_code=503,
            content=NotReadyResponse(
                status="not_ready",
                reason="no_skills_loaded",
            ).model_dump(mode="json"),
        )
    if await probe_cache.is_ready():
        return JSONResponse(
            status_code=200,
            content=ReadyResponse(status="ready").model_dump(mode="json"),
        )
    return JSONResponse(
        status_code=503,
        content=NotReadyResponse(
            status="not_ready",
            reason="ollama_unreachable",
        ).model_dump(mode="json"),
    )

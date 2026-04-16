"""Health and readiness endpoints — mounted at root, outside /api/v1/."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import get_probe_cache
from app.api.probe_cache import (
    ProbeCache,  # runtime: FastAPI resolves Annotated[..., Depends()]
)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is alive."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    probe_cache: Annotated[ProbeCache, Depends(get_probe_cache)],
) -> JSONResponse:
    """Readiness probe gated on Ollama reachability.

    Returns 200 if the last Ollama probe succeeded within the TTL window,
    otherwise 503 with a structured JSON body.
    """
    if await probe_cache.is_ready():
        return JSONResponse(status_code=200, content={"status": "ready"})
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "reason": "ollama_unreachable"},
    )

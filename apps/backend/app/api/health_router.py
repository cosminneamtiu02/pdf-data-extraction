"""Health and readiness endpoints — mounted at root, outside /api/v1/."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import get_probe_cache
from app.api.probe_cache import (
    ProbeCache,  # runtime: FastAPI resolves Annotated[..., Depends()]
)

router = APIRouter(tags=["health"])


class _ReadyResponse(BaseModel):
    status: Literal["ready"]


class _NotReadyResponse(BaseModel):
    status: Literal["not_ready"]
    reason: Literal["ollama_unreachable"]


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is alive."""
    return {"status": "ok"}


@router.get(
    "/ready",
    responses={
        200: {
            "description": "Ollama is reachable and the service is ready.",
            "model": _ReadyResponse,
        },
        503: {
            "description": "Ollama is unreachable; service is not ready.",
            "model": _NotReadyResponse,
        },
    },
)
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

"""Health and readiness endpoints — mounted at root, outside /api/v1/."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is alive."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict[str, str]:
    """Readiness probe.

    v1 minimal stub: returns 200 unconditionally. The feature-dev for
    PDFX-E007-F001 replaces this with an Ollama-probe-gated readiness
    check that returns 503 when the configured Ollama base URL is
    unreachable within a short TTL.
    """
    return {"status": "ready"}

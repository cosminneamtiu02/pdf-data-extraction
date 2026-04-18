"""Response schema for the /ready 503 path — OpenAPI decoration only.

The handler in ``app/api/health_router.py`` constructs its JSON payload
inline; this class exists solely so FastAPI can document the 503 response
shape on ``/ready``. ``reason`` enumerates the two ungreen dimensions of
readiness the handler distinguishes — operator config (``no_skills_loaded``)
and external-dependency health (``ollama_unreachable``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class NotReadyResponse(BaseModel):
    status: Literal["not_ready"]
    # ``no_skills_loaded`` covers the production-container scenario where the
    # image ships ``apps/backend/skills/`` holding only ``.gitkeep`` and the
    # operator has not mounted a real skills directory over it. Without this
    # dimension, ``/ready`` would report green and every extraction request
    # would 404 on skill lookup (issue #108).
    reason: Literal["ollama_unreachable", "no_skills_loaded"]

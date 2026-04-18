"""Response schema for the /ready 200 path — OpenAPI decoration only.

The handler in ``app/api/health_router.py`` constructs its JSON payload
inline; this class exists solely so FastAPI can document the 200 response
shape on ``/ready``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ReadyResponse(BaseModel):
    status: Literal["ready"]

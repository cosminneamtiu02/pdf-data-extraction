"""Error detail schema — a single validation error."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """A single validation error detail."""

    field: str
    reason: str

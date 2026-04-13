"""Error body schema — the error object inside the response."""

from pydantic import BaseModel

from app.schemas.error_detail import ErrorDetail


class ErrorBody(BaseModel):
    """The error object inside the response."""

    code: str
    params: dict[str, str | int | float | bool]
    details: list[ErrorDetail] | None = None
    request_id: str

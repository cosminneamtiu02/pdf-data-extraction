"""Error body schema — the error object inside the response."""

from typing import Any

from pydantic import BaseModel

from app.schemas.error_detail import ErrorDetail


class ErrorBody(BaseModel):
    """The error object inside the response.

    ``params`` is typed ``dict[str, Any]`` because the exception handler
    produces it via ``exc.params.model_dump()``, whose return type is
    ``dict[str, Any]`` and whose runtime values — for any current or future
    ``*Params`` model — can include ``None`` (from ``Optional`` fields),
    nested dicts (from nested ``BaseModel`` fields), and lists. A narrower
    ``str | int | float | bool`` union advertised a closed shape the
    handler could not round-trip (issue #345).
    """

    code: str
    params: dict[str, Any]
    details: list[ErrorDetail] | None = None
    request_id: str

"""Top-level error response schema for OpenAPI documentation.

This model is used as `responses={}` metadata on route decorators so the
generated TypeScript client and Swagger UI know the error shape. The exception
handler in api/errors.py builds error responses directly as JSONResponse dicts
at runtime. ErrorResponse is never instantiated in application code.
"""

from pydantic import BaseModel

from app.schemas.error_body import ErrorBody


class ErrorResponse(BaseModel):
    """Top-level error response shape."""

    error: ErrorBody

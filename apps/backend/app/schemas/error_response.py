"""Top-level error response schema for OpenAPI documentation.

This model is used as `responses={}` metadata on route decorators so the
generated TypeScript client and Swagger UI know the error shape. The
``DomainError`` handler in ``api/errors.py`` also instantiates it at runtime
so Pydantic asserts the constructed envelope matches the declared schema
before it ships (issue #345).
"""

from pydantic import BaseModel

from app.schemas.error_body import ErrorBody


class ErrorResponse(BaseModel):
    """Top-level error response shape."""

    error: ErrorBody

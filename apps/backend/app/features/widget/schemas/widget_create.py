"""Schema for creating a Widget."""

from pydantic import BaseModel, Field


class WidgetCreate(BaseModel):
    """Fields the client sends to create a widget.

    Note: name max_length (255) is enforced in the service layer via
    WidgetNameTooLongError, not via Pydantic, so the error response
    carries rich context (name, max_length, actual_length).
    min_length=1 ensures empty strings are rejected at validation level.
    """

    name: str = Field(min_length=1)
    description: str | None = None

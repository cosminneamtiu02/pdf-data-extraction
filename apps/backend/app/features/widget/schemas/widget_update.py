"""Schema for updating a Widget (PATCH semantics — all fields optional)."""

from pydantic import BaseModel, Field


class WidgetUpdate(BaseModel):
    """Fields the client sends to update a widget.

    All fields optional for PATCH. Absent fields are not changed.
    name uses min_length=1 to prevent clearing a required DB field.
    description is nullable in DB so None is allowed.
    """

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None

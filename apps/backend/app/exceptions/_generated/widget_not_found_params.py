"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class WidgetNotFoundParams(BaseModel):
    """Parameters for WIDGET_NOT_FOUND error."""

    widget_id: str

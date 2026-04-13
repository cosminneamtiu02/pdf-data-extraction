"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class WidgetNameConflictParams(BaseModel):
    """Parameters for WIDGET_NAME_CONFLICT error."""

    name: str

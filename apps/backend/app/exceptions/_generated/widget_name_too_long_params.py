"""Generated from errors.yaml. Do not edit."""

from pydantic import BaseModel


class WidgetNameTooLongParams(BaseModel):
    """Parameters for WIDGET_NAME_TOO_LONG error."""

    name: str
    max_length: int
    actual_length: int

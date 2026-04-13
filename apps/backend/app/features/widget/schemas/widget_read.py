"""Schema for reading a Widget."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class WidgetRead(BaseModel):
    """Fields the client receives when reading a widget."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

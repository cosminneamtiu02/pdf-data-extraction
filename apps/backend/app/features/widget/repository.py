"""Repository for Widget entity."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.widget.model import Widget
from app.shared.base_repository import BaseRepository


class WidgetRepository(BaseRepository[Widget]):
    """Data access layer for Widget."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Widget)

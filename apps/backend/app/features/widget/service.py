"""Service for Widget entity — business logic layer."""

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.exceptions import (
    NotFoundError,
    WidgetNameConflictError,
    WidgetNameTooLongError,
    WidgetNotFoundError,
)
from app.features.widget.model import Widget
from app.features.widget.repository import WidgetRepository
from app.features.widget.schemas.widget_create import WidgetCreate
from app.features.widget.schemas.widget_read import WidgetRead
from app.features.widget.schemas.widget_update import WidgetUpdate
from app.shared.base_service import BaseService

WIDGET_NAME_MAX_LENGTH = 255


class WidgetService(BaseService[Widget, WidgetCreate, WidgetRead, WidgetUpdate]):
    """Business logic for Widget CRUD."""

    repository: WidgetRepository

    def _to_read(self, model: Widget) -> WidgetRead:
        return WidgetRead.model_validate(model, from_attributes=True)

    async def create(self, data: WidgetCreate) -> WidgetRead:
        """Create a widget with name length and uniqueness validation."""
        if len(data.name) > WIDGET_NAME_MAX_LENGTH:
            raise WidgetNameTooLongError(
                name=data.name,
                max_length=WIDGET_NAME_MAX_LENGTH,
                actual_length=len(data.name),
            )
        try:
            return await super().create(data)
        except IntegrityError as e:
            if "uq_widgets_name" in str(e).lower():
                raise WidgetNameConflictError(name=data.name) from e
            raise

    async def get_by_id(self, entity_id: UUID) -> WidgetRead:
        """Get a widget by ID, raising WidgetNotFoundError if missing."""
        try:
            return await super().get_by_id(entity_id)
        except NotFoundError:
            raise WidgetNotFoundError(widget_id=str(entity_id)) from None

    async def update(self, entity_id: UUID, data: WidgetUpdate) -> WidgetRead:
        """Update a widget, raising WidgetNotFoundError if missing."""
        if data.name is not None and len(data.name) > WIDGET_NAME_MAX_LENGTH:
            raise WidgetNameTooLongError(
                name=data.name,
                max_length=WIDGET_NAME_MAX_LENGTH,
                actual_length=len(data.name),
            )
        try:
            return await super().update(entity_id, data)
        except NotFoundError:
            raise WidgetNotFoundError(widget_id=str(entity_id)) from None

    async def delete(self, entity_id: UUID) -> None:
        """Delete a widget, raising WidgetNotFoundError if missing."""
        try:
            await super().delete(entity_id)
        except NotFoundError:
            raise WidgetNotFoundError(widget_id=str(entity_id)) from None

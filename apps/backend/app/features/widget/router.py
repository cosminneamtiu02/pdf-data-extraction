"""HTTP endpoints for Widget CRUD."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.features.widget.repository import WidgetRepository
from app.features.widget.schemas.widget_create import WidgetCreate
from app.features.widget.schemas.widget_read import WidgetRead
from app.features.widget.schemas.widget_update import WidgetUpdate
from app.features.widget.service import WidgetService
from app.schemas.page import Page

router = APIRouter(prefix="/widgets", tags=["widgets"])


def get_widget_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WidgetService:
    """Depends factory: wire WidgetRepository -> WidgetService."""
    repository = WidgetRepository(session)
    return WidgetService(repository)


WidgetServiceDep = Annotated[WidgetService, Depends(get_widget_service)]


@router.post("", status_code=201)
async def create_widget(
    widget_in: WidgetCreate,
    service: WidgetServiceDep,
) -> WidgetRead:
    """Create a new widget."""
    return await service.create(widget_in)


@router.get("")
async def list_widgets(
    service: WidgetServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[WidgetRead]:
    """List widgets with pagination."""
    return await service.list(page=page, size=size)


@router.get("/{widget_id}")
async def get_widget(
    widget_id: UUID,
    service: WidgetServiceDep,
) -> WidgetRead:
    """Get a widget by ID."""
    return await service.get_by_id(widget_id)


@router.patch("/{widget_id}")
async def update_widget(
    widget_id: UUID,
    widget_in: WidgetUpdate,
    service: WidgetServiceDep,
) -> WidgetRead:
    """Update a widget (PATCH -- partial update)."""
    return await service.update(widget_id, widget_in)


@router.delete("/{widget_id}", status_code=204)
async def delete_widget(
    widget_id: UUID,
    service: WidgetServiceDep,
) -> None:
    """Delete a widget."""
    await service.delete(widget_id)

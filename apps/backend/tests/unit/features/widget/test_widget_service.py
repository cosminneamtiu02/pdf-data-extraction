"""Unit tests for WidgetService with mocked repository."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions import NotFoundError
from app.features.widget.schemas.widget_create import WidgetCreate
from app.features.widget.schemas.widget_read import WidgetRead
from app.features.widget.schemas.widget_update import WidgetUpdate
from app.features.widget.service import WidgetService


def _make_mock_widget(*, name="Test Widget", description=None):
    """Create a mock Widget model object."""
    widget = MagicMock()
    widget.id = uuid.uuid4()
    widget.name = name
    widget.description = description
    widget.created_at = datetime.now(UTC)
    widget.updated_at = datetime.now(UTC)
    return widget


def _make_service(mock_repo=None):
    """Create a WidgetService with a mocked repository."""
    repo = mock_repo or AsyncMock()
    return WidgetService(repo), repo


async def test_widget_service_create_returns_widget_read():
    """Create should return a WidgetRead from the created model."""
    service, repo = _make_service()
    mock_widget = _make_mock_widget(name="New Widget")
    repo.create = AsyncMock(return_value=mock_widget)

    result = await service.create(WidgetCreate(name="New Widget"))

    assert isinstance(result, WidgetRead)
    assert result.name == "New Widget"
    repo.create.assert_called_once()


async def test_widget_service_get_by_id_returns_widget_read():
    """Get by ID should return a WidgetRead."""
    service, repo = _make_service()
    mock_widget = _make_mock_widget()
    repo.get_by_id = AsyncMock(return_value=mock_widget)

    result = await service.get_by_id(mock_widget.id)

    assert isinstance(result, WidgetRead)
    assert result.id == mock_widget.id


async def test_widget_service_get_by_id_raises_not_found():
    """Get by ID should propagate NotFoundError as WidgetNotFoundError."""
    service, repo = _make_service()
    widget_id = uuid.uuid4()
    repo.get_by_id = AsyncMock(side_effect=NotFoundError())

    from app.exceptions import WidgetNotFoundError

    with pytest.raises(WidgetNotFoundError):
        await service.get_by_id(widget_id)


async def test_widget_service_update_returns_updated_read():
    """Update should return an updated WidgetRead."""
    service, repo = _make_service()
    mock_widget = _make_mock_widget(name="Updated")
    repo.get_by_id = AsyncMock(return_value=mock_widget)
    repo.update = AsyncMock(return_value=mock_widget)

    result = await service.update(mock_widget.id, WidgetUpdate(name="Updated"))

    assert isinstance(result, WidgetRead)
    assert result.name == "Updated"


async def test_widget_service_delete_delegates_to_repository():
    """Delete should call repository delete."""
    service, repo = _make_service()
    widget_id = uuid.uuid4()
    repo.delete = AsyncMock()

    await service.delete(widget_id)

    repo.delete.assert_called_once_with(widget_id)

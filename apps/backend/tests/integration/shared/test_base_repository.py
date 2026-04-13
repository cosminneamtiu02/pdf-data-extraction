"""Integration tests for BaseRepository using DummyModel."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.shared.base_repository import BaseRepository
from tests.integration.shared.dummy_model import DummyModel


class DummyRepository(BaseRepository[DummyModel]):
    """Test-only repository for DummyModel."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DummyModel)


async def test_base_repository_create_persists(session: AsyncSession):
    """Create should persist a new entity to the database."""
    repo = DummyRepository(session)
    entity = await repo.create(name="test-item")
    assert entity.id is not None
    assert entity.name == "test-item"
    assert entity.created_at is not None


async def test_base_repository_get_by_id_returns(session: AsyncSession):
    """Get by ID should return the entity when it exists."""
    repo = DummyRepository(session)
    created = await repo.create(name="findable")
    found = await repo.get_by_id(created.id)
    assert found.id == created.id
    assert found.name == "findable"


async def test_base_repository_get_by_id_raises_not_found(session: AsyncSession):
    """Get by ID should raise NotFoundError when entity doesn't exist."""
    repo = DummyRepository(session)
    with pytest.raises(NotFoundError):
        await repo.get_by_id(uuid.uuid4())


async def test_base_repository_list_returns_items_and_total_count(session: AsyncSession):
    """List should return items and total count for pagination."""
    repo = DummyRepository(session)
    for i in range(5):
        await repo.create(name=f"item-{i}")

    items, total = await repo.list(page=1, size=3)
    assert len(items) == 3
    assert total >= 5


async def test_base_repository_list_returns_empty_when_no_data(session: AsyncSession):
    """List should return empty when no matching data (using high page number)."""
    repo = DummyRepository(session)
    items, _total = await repo.list(page=9999, size=20)
    assert items == []
    # total still counts all rows, but items on this page are empty


async def test_base_repository_update_modifies(session: AsyncSession):
    """Update should modify fields on the entity."""
    repo = DummyRepository(session)
    created = await repo.create(name="original")
    updated = await repo.update(created.id, name="modified")
    assert updated.name == "modified"
    assert updated.id == created.id


async def test_base_repository_update_raises_not_found(session: AsyncSession):
    """Update should raise NotFoundError when entity doesn't exist."""
    repo = DummyRepository(session)
    with pytest.raises(NotFoundError):
        await repo.update(uuid.uuid4(), name="nope")


async def test_base_repository_delete_removes(session: AsyncSession):
    """Delete should remove the entity from the database."""
    repo = DummyRepository(session)
    created = await repo.create(name="deletable")
    await repo.delete(created.id)
    with pytest.raises(NotFoundError):
        await repo.get_by_id(created.id)


async def test_base_repository_delete_raises_not_found(session: AsyncSession):
    """Delete should raise NotFoundError when entity doesn't exist."""
    repo = DummyRepository(session)
    with pytest.raises(NotFoundError):
        await repo.delete(uuid.uuid4())

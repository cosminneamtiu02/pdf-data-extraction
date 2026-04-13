"""Integration tests for BaseService using DummyModel."""

import uuid

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.schemas.page import Page
from app.shared.base_repository import BaseRepository
from app.shared.base_service import BaseService
from tests.integration.shared.dummy_model import DummyModel


class DummyCreate(BaseModel):
    name: str


class DummyRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    name: str


class DummyUpdate(BaseModel):
    name: str | None = None


class DummyRepository(BaseRepository[DummyModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DummyModel)


class DummyService(BaseService[DummyModel, DummyCreate, DummyRead, DummyUpdate]):
    def _to_read(self, model: DummyModel) -> DummyRead:
        return DummyRead.model_validate(model, from_attributes=True)


async def test_base_service_create_persists_and_returns(session: AsyncSession):
    """Service create should persist to DB and return read schema."""
    repo = DummyRepository(session)
    service = DummyService(repo)

    result = await service.create(DummyCreate(name="svc-test"))
    assert isinstance(result, DummyRead)
    assert result.name == "svc-test"
    assert result.id is not None


async def test_base_service_full_crud_lifecycle(session: AsyncSession):
    """Service should support full create -> get -> list -> update -> delete."""
    repo = DummyRepository(session)
    service = DummyService(repo)

    # Create
    created = await service.create(DummyCreate(name="lifecycle-test"))
    assert created.name == "lifecycle-test"

    # Get by ID
    fetched = await service.get_by_id(created.id)
    assert fetched.id == created.id
    assert fetched.name == "lifecycle-test"

    # List
    page = await service.list(page=1, size=10)
    assert isinstance(page, Page)
    assert page.total >= 1
    assert any(item.id == created.id for item in page.items)

    # Update
    updated = await service.update(created.id, DummyUpdate(name="updated-name"))
    assert updated.name == "updated-name"
    assert updated.id == created.id

    # Delete
    await service.delete(created.id)

    # Verify deleted
    with pytest.raises(NotFoundError):
        await service.get_by_id(created.id)

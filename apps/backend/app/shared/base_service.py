"""Generic async service base class."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel

from app.schemas.page import Page
from app.shared.base_model import Base
from app.shared.base_repository import BaseRepository

ModelT = TypeVar("ModelT", bound=Base)
CreateT = TypeVar("CreateT", bound=BaseModel)
ReadT = TypeVar("ReadT", bound=BaseModel)
UpdateT = TypeVar("UpdateT", bound=BaseModel)


class BaseService(ABC, Generic[ModelT, CreateT, ReadT, UpdateT]):
    """Base service wrapping a repository with CRUD operations.

    Converts between SQLAlchemy models and Pydantic schemas.
    Subclasses MUST override _to_read().
    """

    def __init__(self, repository: BaseRepository[ModelT]) -> None:
        self.repository = repository

    @abstractmethod
    def _to_read(self, model: ModelT) -> ReadT:
        """Convert a SQLAlchemy model to a read schema."""
        ...

    async def create(self, data: CreateT) -> ReadT:
        """Create a new entity and return the read schema."""
        # exclude_unset=True so fields with server-side defaults aren't overridden with None
        model = await self.repository.create(**data.model_dump(exclude_unset=True))
        return self._to_read(model)

    async def get_by_id(self, entity_id: UUID) -> ReadT:
        """Get an entity by ID and return the read schema."""
        model = await self.repository.get_by_id(entity_id)
        return self._to_read(model)

    async def list(self, *, page: int = 1, size: int = 20) -> Page[ReadT]:
        """List entities with pagination."""
        items, total = await self.repository.list(page=page, size=size)
        return Page.create(  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            items=[self._to_read(item) for item in items],
            total=total,
            page=page,
            size=size,
        )

    async def update(self, entity_id: UUID, data: UpdateT) -> ReadT:
        """Update an entity and return the read schema."""
        update_data = data.model_dump(exclude_unset=True)
        model = await self.repository.update(entity_id, **update_data)
        return self._to_read(model)

    async def delete(self, entity_id: UUID) -> None:
        """Delete an entity."""
        await self.repository.delete(entity_id)

"""Generic async repository base class."""

from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError
from app.shared.base_model import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Base repository providing CRUD operations for SQLAlchemy models.

    Subclasses inherit all CRUD methods and can add entity-specific queries.
    """

    def __init__(self, session: AsyncSession, model_class: type[ModelT]) -> None:
        self.session = session
        self.model_class = model_class

    async def create(self, **kwargs: Any) -> ModelT:
        """Create and persist a new entity."""
        entity = self.model_class(**kwargs)
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def get_by_id(self, entity_id: UUID) -> ModelT:
        """Get an entity by ID. Raises NotFoundError if not found."""
        stmt = select(self.model_class).where(self.model_class.id == entity_id)
        result = await self.session.execute(stmt)
        entity = result.scalar_one_or_none()
        if entity is None:
            raise NotFoundError
        return entity

    async def list(self, *, page: int = 1, size: int = 20) -> tuple[list[ModelT], int]:
        """List entities with offset-based pagination.

        Returns (items, total_count).
        """
        # Count total
        count_stmt = select(func.count()).select_from(self.model_class)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        # Fetch page
        offset = (page - 1) * size
        items_stmt = (
            select(self.model_class)
            .order_by(self.model_class.created_at)
            .offset(offset)
            .limit(size)
        )
        items_result = await self.session.execute(items_stmt)
        items = list(items_result.scalars().all())

        return items, total

    async def update(self, entity_id: UUID, **kwargs: Any) -> ModelT:
        """Update an entity's fields. Raises NotFoundError if not found."""
        entity = await self.get_by_id(entity_id)
        for key, value in kwargs.items():
            setattr(entity, key, value)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(self, entity_id: UUID) -> None:
        """Delete an entity. Raises NotFoundError if not found."""
        entity = await self.get_by_id(entity_id)
        await self.session.delete(entity)
        await self.session.flush()

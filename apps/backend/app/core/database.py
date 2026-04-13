"""Async database engine and session management."""

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

# Module-level engine reference for clean shutdown disposal
_engine: AsyncEngine | None = None


def _create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async SQLAlchemy engine with production-safe pool settings."""
    global _engine  # noqa: PLW0603
    _engine = create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        connect_args={"server_settings": {"statement_timeout": "30000"}},  # 30s
    )
    return _engine


@lru_cache(maxsize=1)
def _get_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Create and cache a session factory."""
    engine = _create_engine(database_url)
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@lru_cache(maxsize=1)
def _get_settings() -> Settings:
    """Internal dependency for settings. Cached — env parsed once."""
    return Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env


async def dispose_engine() -> None:
    """Dispose the engine and clear caches. Called during shutdown."""
    global _engine  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _get_session_factory.cache_clear()


async def get_session(
    settings: Annotated[Settings, Depends(_get_settings)],
) -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency that yields a database session.

    Commits on success, rolls back on exception.
    """
    factory = _get_session_factory(settings.database_url)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

"""Integration test fixtures — Testcontainers Postgres."""

from collections.abc import AsyncGenerator, Generator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from app.core.database import _get_settings, get_session
from app.features.widget.model import Widget  # noqa: F401
from app.shared.base_model import Base
from tests.integration.shared.dummy_model import DummyModel  # noqa: F401


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer]:
    """Start a Postgres 17 container once for the entire test session."""
    with PostgresContainer("postgres:17", driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    """Get the connection URL for the test container."""
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
def async_engine(database_url: str):
    """Create an async engine connected to the test container."""
    return create_async_engine(database_url, echo=False, pool_size=5)


@pytest.fixture(scope="session")
def session_factory(async_engine):
    """Create a session factory for the test engine."""
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture(scope="session")
async def _create_tables(async_engine) -> AsyncGenerator[None]:
    """Create all tables once at the start of the test session."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
async def _cleanup_tables(async_engine, _create_tables: None):
    """Truncate all tables after each test for isolation."""
    yield
    async with async_engine.begin() as conn:
        # Truncate all tables in reverse dependency order
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f"TRUNCATE TABLE {table.name} CASCADE"))


@pytest.fixture
async def session(
    session_factory,
    _create_tables: None,
) -> AsyncGenerator[AsyncSession]:
    """Provide a session for direct database access in tests."""
    async with session_factory() as sess:
        yield sess


@pytest.fixture
async def client(
    session_factory,
    _create_tables: None,
) -> AsyncGenerator[AsyncClient]:
    """Provide an httpx AsyncClient wired to the FastAPI app.

    Each request gets its own session from the factory — matching production
    behavior. Sessions auto-commit on success. Test isolation is provided
    by table truncation after each test.
    """
    from app.main import app

    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    from app.core.config import Settings

    def override_get_settings() -> Settings:
        return Settings(database_url="not-used-overridden")

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[_get_settings] = override_get_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()

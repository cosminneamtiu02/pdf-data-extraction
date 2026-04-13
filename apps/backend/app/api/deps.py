"""Shared FastAPI dependencies."""

from functools import lru_cache

from app.core.config import Settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env

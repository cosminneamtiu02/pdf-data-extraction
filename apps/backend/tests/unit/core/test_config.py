"""Tests for the application configuration."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_validates_required_database_url():
    """Settings should fail without DATABASE_URL."""
    with pytest.raises(ValidationError):
        Settings(database_url=None)  # type: ignore[arg-type]


def test_settings_accepts_valid_config():
    """Settings should construct with all required fields."""
    s = Settings(
        database_url="postgresql+asyncpg://user:pass@localhost:5432/db",
        app_env="development",
        log_level="info",
        cors_origins=["http://localhost:5173"],
    )
    assert s.database_url == "postgresql+asyncpg://user:pass@localhost:5432/db"
    assert s.app_env == "development"
    assert s.log_level == "info"
    assert s.cors_origins == ["http://localhost:5173"]

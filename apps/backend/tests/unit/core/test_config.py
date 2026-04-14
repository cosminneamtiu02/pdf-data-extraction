"""Tests for the application configuration."""

from app.core.config import Settings


def test_settings_defaults() -> None:
    """Settings should construct with sensible defaults from environment or code."""
    s = Settings()
    assert s.app_env == "development"
    assert s.log_level == "info"
    assert s.cors_origins == ["http://localhost:5173"]


def test_settings_accepts_overrides() -> None:
    """Settings should accept explicit overrides."""
    s = Settings(
        app_env="production",
        log_level="warning",
        cors_origins=["https://example.com"],
    )
    assert s.app_env == "production"
    assert s.log_level == "warning"
    assert s.cors_origins == ["https://example.com"]

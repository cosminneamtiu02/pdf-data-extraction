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


def test_settings_redaction_defaults() -> None:
    """log_redacted_keys and log_max_value_length default to the documented values."""
    s = Settings()
    assert s.log_max_value_length == 500
    assert s.log_redacted_keys == [
        "pdf_bytes",
        "raw_output",
        "extracted_value",
        "prompt",
        "field_values",
    ]


def test_settings_redaction_overrides() -> None:
    """Both redaction fields accept explicit overrides."""
    s = Settings(log_max_value_length=42, log_redacted_keys=["secret", "token"])
    assert s.log_max_value_length == 42
    assert s.log_redacted_keys == ["secret", "token"]


def test_settings_ollama_probe_ttl_default() -> None:
    """ollama_probe_ttl_seconds defaults to 10.0."""
    s = Settings()
    assert s.ollama_probe_ttl_seconds == 10.0


def test_settings_ollama_probe_timeout_default() -> None:
    """ollama_probe_timeout_seconds defaults to 5.0."""
    s = Settings()
    assert s.ollama_probe_timeout_seconds == 5.0

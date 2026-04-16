"""Tests for the application configuration."""

import pytest
from pydantic import ValidationError

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


# -- ollama_base_url validation (issue #64) --------------------------------


def test_settings_ollama_base_url_empty_string_rejected() -> None:
    """An empty string for ollama_base_url must be rejected."""
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(ollama_base_url="")


def test_settings_ollama_base_url_whitespace_only_rejected() -> None:
    """A whitespace-only string for ollama_base_url must be rejected."""
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(ollama_base_url="   ")


def test_settings_ollama_base_url_no_http_scheme_rejected() -> None:
    """A URL without http:// or https:// scheme must be rejected."""
    with pytest.raises(ValidationError, match="must start with"):
        Settings(ollama_base_url="ftp://localhost:11434")


def test_settings_ollama_base_url_bare_hostname_rejected() -> None:
    """A bare hostname without scheme must be rejected."""
    with pytest.raises(ValidationError, match="must start with"):
        Settings(ollama_base_url="localhost:11434")


def test_settings_ollama_base_url_scheme_only_no_host_rejected() -> None:
    """http:// with no host must be rejected."""
    with pytest.raises(ValidationError, match="must include a host"):
        Settings(ollama_base_url="http://")


def test_settings_ollama_base_url_trailing_api_path_rejected() -> None:
    """A URL ending with /api must be rejected to prevent double /api segments."""
    with pytest.raises(ValidationError, match="must not include a trailing /api"):
        Settings(ollama_base_url="http://localhost:11434/api")


def test_settings_ollama_base_url_valid_http_accepted() -> None:
    """A valid http:// URL must be accepted."""
    s = Settings(ollama_base_url="http://localhost:11434")
    assert s.ollama_base_url == "http://localhost:11434"


def test_settings_ollama_base_url_valid_https_accepted() -> None:
    """A valid https:// URL must be accepted."""
    s = Settings(ollama_base_url="https://ollama.example.com")
    assert s.ollama_base_url == "https://ollama.example.com"


def test_settings_ollama_base_url_trailing_slash_stripped() -> None:
    """Trailing slashes must be stripped from ollama_base_url."""
    s = Settings(ollama_base_url="http://localhost:11434/")
    assert s.ollama_base_url == "http://localhost:11434"


def test_settings_ollama_base_url_whitespace_stripped() -> None:
    """Leading/trailing whitespace must be stripped before validation."""
    s = Settings(ollama_base_url="  http://localhost:11434  ")
    assert s.ollama_base_url == "http://localhost:11434"


# -- cors_origins empty-string coercion (issue #58) --------------------------


def test_settings_cors_origins_empty_string_coerced_to_empty_list() -> None:
    """A programmatic empty string for cors_origins must become an empty list.

    This covers direct ``Settings(cors_origins="")`` input.  The production
    env-var path is handled by the compose default ``${CORS_ORIGINS:-[]}``;
    the validator catches the programmatic case where callers may still
    provide an empty string.
    """
    s = Settings(cors_origins="")  # type: ignore[arg-type]
    assert s.cors_origins == []


def test_settings_cors_origins_json_empty_array_accepted() -> None:
    """A JSON '[]' string for cors_origins must parse to an empty list."""
    s = Settings(cors_origins="[]")  # type: ignore[arg-type]
    assert s.cors_origins == []


def test_settings_cors_origins_json_array_parsed() -> None:
    """A JSON array string for cors_origins must parse correctly."""
    s = Settings(cors_origins='["https://a.com","https://b.com"]')  # type: ignore[arg-type]
    assert s.cors_origins == ["https://a.com", "https://b.com"]


def test_settings_cors_origins_native_list_passthrough() -> None:
    """A native Python list for cors_origins must pass through unchanged."""
    s = Settings(cors_origins=["https://example.com"])
    assert s.cors_origins == ["https://example.com"]


def test_settings_cors_origins_malformed_json_raises_clear_error() -> None:
    """A string starting with '[' but not valid JSON must produce a clear error."""
    with pytest.raises(ValidationError, match="cors_origins must be a JSON array"):
        Settings(cors_origins="[not valid json")  # type: ignore[arg-type]


def test_settings_cors_origins_empty_array_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production path: compose defaults CORS_ORIGINS to '[]' when unset.

    pydantic-settings JSON-parses env vars for complex types *before*
    field validators run, so an empty string cannot be intercepted by
    a @field_validator.  The compose default ``${CORS_ORIGINS:-[]}``
    ensures the env var is always a valid JSON array.
    """
    monkeypatch.setenv("CORS_ORIGINS", "[]")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_origins == []

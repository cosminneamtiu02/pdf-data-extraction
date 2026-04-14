"""Application configuration via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    app_env: str = "development"
    log_level: str = "info"
    cors_origins: list[str] = ["http://localhost:5173"]

    log_max_value_length: int = 500
    log_redacted_keys: list[str] = [
        "pdf_bytes",
        "raw_output",
        "extracted_value",
        "prompt",
        "field_values",
    ]

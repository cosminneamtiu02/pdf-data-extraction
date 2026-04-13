"""Application configuration via pydantic-settings."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    database_url: str
    app_env: str = "development"
    log_level: str = "info"
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Reject non-PostgreSQL URLs at startup."""
        if not v.startswith("postgresql"):
            msg = (
                f"DATABASE_URL must start with 'postgresql' (got '{v[:20]}...'). "
                "SQLite is not supported. See CLAUDE.md."
            )
            raise ValueError(msg)
        return v

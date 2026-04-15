"""Application configuration via pydantic-settings."""

from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.docling_modes import OcrMode, TableMode

# `app/core/config.py` -> `app/core/` -> `app/` -> `apps/backend/`
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SKILLS_DIR = _BACKEND_ROOT / "skills"


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
    structured_output_max_retries: Annotated[int, Field(ge=0)] = 3

    skills_dir: Path = Field(default_factory=lambda: _DEFAULT_SKILLS_DIR)
    docling_ocr_default: OcrMode = "auto"
    docling_table_mode_default: TableMode = "fast"

    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "gemma4:e2b"
    ollama_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0

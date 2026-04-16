"""Application configuration via pydantic-settings."""

import json
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, field_validator
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
    max_pdf_pages: Annotated[int, Field(gt=0)] = 200
    max_pdf_bytes: Annotated[int, Field(gt=0)] = 50 * 1024 * 1024  # 50 MB

    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "gemma4:e2b"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _coerce_cors_origins(cls, v: object) -> object:
        """Coerce an empty string to an empty list.

        docker-compose.prod.yml injects ``CORS_ORIGINS=${CORS_ORIGINS}``.
        When the host env var is absent, Compose substitutes an empty string
        which crashes Pydantic's ``list[str]`` JSON-mode parsing.  This
        validator converts ``""`` -> ``[]`` before Pydantic sees the value.
        Non-empty strings that look like JSON arrays are also decoded here so
        the env-var path works identically to the programmatic path.
        """
        if isinstance(v, str):
            stripped = v.strip()
            if stripped == "":
                return []
            if stripped.startswith("["):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError as exc:
                    msg = 'cors_origins must be a JSON array string (e.g. ["https://example.com"])'
                    raise ValueError(msg) from exc
        return v

    @field_validator("ollama_base_url")
    @classmethod
    def _validate_ollama_base_url(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            msg = "ollama_base_url must not be empty"
            raise ValueError(msg)
        if not stripped.startswith(("http://", "https://")):
            msg = "ollama_base_url must start with http:// or https://"
            raise ValueError(msg)
        parsed = urlsplit(stripped)
        if not parsed.netloc:
            msg = "ollama_base_url must include a host (e.g. http://localhost:11434)"
            raise ValueError(msg)
        if parsed.path.rstrip("/").endswith("/api"):
            msg = (
                "ollama_base_url must not include a trailing /api path; "
                "the client appends /api/generate and /api/tags automatically"
            )
            raise ValueError(msg)
        return stripped.rstrip("/")

    ollama_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    ollama_probe_ttl_seconds: Annotated[float, Field(ge=0)] = 10.0
    ollama_probe_timeout_seconds: Annotated[float, Field(gt=0)] = 5.0

    # Extraction pipeline (PDFX-E006-F002)
    extraction_timeout_seconds: Annotated[float, Field(gt=0)] = 180.0

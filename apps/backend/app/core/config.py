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

# Canonical stdlib-logging level names accepted by this validator.
# Aliases such as ``WARN`` and ``FATAL`` are intentionally excluded —
# operators should spell ``WARNING`` in full so the error message is
# unambiguous.
_VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Reject unknown fields loaded from ``.env`` / constructor kwargs so
        # typos (``OLAMA_MODEL=gemma4:e2b``) raise ``ValidationError`` at
        # startup instead of silently falling back to the default. Arbitrary
        # shell env vars unrelated to ``Settings`` fields remain ignored —
        # pydantic-settings only consults names that match declared fields
        # (issue #271). Declared explicitly so the contract is part of this
        # file's source of truth and does not depend on the pydantic-settings
        # upstream default staying ``"forbid"`` across upgrades.
        extra="forbid",
    )

    app_env: str = "development"
    log_level: str = "info"
    cors_origins: list[str] = ["http://localhost:5173"]
    # CORS method / header allowlists. Default to the current route surface
    # (GET for /health, /ready, /openapi.json etc.; POST for /api/v1/extract)
    # plus the minimum headers the app consumes. Hardcoding ``["*"]`` here
    # previously accepted any verb and any request header (including
    # ``Authorization``) regardless of how tightly operators scoped
    # ``cors_origins`` — see issue #211.
    cors_methods: list[str] = ["GET", "HEAD", "POST"]
    cors_headers: list[str] = ["Authorization", "Content-Type", "X-Request-Id"]

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

    @field_validator("log_level", mode="after")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        """Normalize and validate ``log_level`` against stdlib-logging levels.

        Without this validator, a typo like ``LOG_LEVEL=debg`` slipped
        past ``Settings`` construction and only crashed later when
        structlog / ``logging.Logger.setLevel`` tried to parse it —
        *before* the FastAPI exception handlers install, producing an
        opaque traceback on startup (issue #146).

        The validator:

        * accepts any case (``debug``, ``Debug``, ``DEBUG``);
        * strips surrounding whitespace (common ``.env`` paper-cut);
        * rejects the empty string, non-standard aliases (``warn``),
          and typos, with a message that lists the valid options so
          operators can self-correct.

        ``mode="after"`` is safe because the field is typed ``str`` and
        pydantic-settings coerces env vars to strings before this
        validator runs.
        """
        normalized = v.strip().upper()
        if normalized not in _VALID_LOG_LEVELS:
            valid = ", ".join(sorted(_VALID_LOG_LEVELS))
            msg = f"log_level must be one of {{{valid}}} (case-insensitive); got {v!r}"
            raise ValueError(msg)
        return normalized

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _coerce_cors_origins(cls, v: object) -> object:
        """Support string inputs for ``cors_origins``.

        In production, docker-compose now uses ``${CORS_ORIGINS:-[]}``,
        so a missing ``CORS_ORIGINS`` env var defaults to ``[]`` and
        avoids the previous startup crash.

        This validator is primarily for programmatic
        ``Settings(cors_origins="...")`` usage: it converts ``""`` to
        ``[]`` and decodes JSON array strings so string inputs behave
        like native ``list[str]`` values.
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

    # Extraction admission control (issue #109)
    # Hard cap on concurrent extraction pipelines running inside a single
    # process. When the cap is reached, further requests are rejected
    # immediately with EXTRACTION_OVERLOADED (HTTP 503) — they are not
    # queued on a semaphore wait list. Queuing would pile up callers behind
    # their own 504 timeout budget and defeat the backpressure contract.
    max_concurrent_extractions: Annotated[int, Field(gt=0)] = 4

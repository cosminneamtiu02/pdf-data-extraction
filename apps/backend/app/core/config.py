"""Application configuration via pydantic-settings."""

import json
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.core.docling_modes import OcrMode, TableMode
from app.core.filtered_dotenv_source import FilteredDotEnvSettingsSource

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
        # Rejects unknown inputs at ``Settings`` construction. What this
        # catches and what it does NOT catch:
        #
        #   * CAUGHT — unknown constructor kwargs (e.g. a future
        #     ``Settings(olama_model=...)`` site in code) raise
        #     ``ValidationError``. This is the load-bearing guard for
        #     issue #271.
        #   * NOT CAUGHT — unknown keys in ``.env``. The
        #     ``FilteredDotEnvSettingsSource`` below strips dotenv keys
        #     that are not declared fields BEFORE they reach validation,
        #     so ``BENCH_*`` keys owned by ``BenchmarkSettings`` (and
        #     typos like ``OLLMA_MODEL=x``) are silently ignored. This
        #     mirrors the shell-env-var behavior below and preserves
        #     the shared-``.env`` layout (``cp .env.example .env`` in
        #     ``docs/new-project-setup.md``).
        #   * NOT CAUGHT — typoed shell env var names. pydantic-settings
        #     resolves env vars by name-lookup: it only reads names that
        #     match declared fields. A misspelled shell env var like
        #     ``OLAMA_MODEL=x`` (one ``L``) is never read, so ``extra``
        #     has nothing to forbid.
        #
        # The real defence against typoed env-var names (shell and
        # ``.env`` alike) is the parity check in
        # ``tests/unit/core/test_env_example_parity.py``, which fails
        # on any ``.env.example`` key that does not map to a declared
        # field on a managed ``BaseSettings`` subclass. Operators who
        # typo keys in their own local ``.env`` (not the checked-in
        # example) get the silent-ignore behavior pydantic-settings
        # ships by default for env vars — tracked separately if the
        # project adopts a deploy-time lint.
        #
        # Declared explicitly so the contract is part of this file's
        # source of truth and does not depend on the pydantic-settings
        # upstream default staying ``"forbid"`` across upgrades (issue
        # #271). See ``tests/unit/core/test_settings_extra_forbid.py``
        # for the full matrix of catches-vs-does-not-catch cases.
        extra="forbid",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Swap the default dotenv source for one that filters unknown keys.

        The default :class:`~pydantic_settings.DotEnvSettingsSource`
        returns every key from ``.env``, which collides with
        ``extra="forbid"`` because our ``.env`` is shared with
        :class:`scripts.BenchmarkSettings` and contains its ``BENCH_*``
        keys. ``FilteredDotEnvSettingsSource`` strips keys that are not
        declared on :class:`Settings` before they reach validation,
        preserving the ``extra="forbid"`` guard for constructor kwargs
        while allowing the shared ``.env`` layout documented in
        ``docs/new-project-setup.md`` to keep working.

        The replacement source is constructed with the ``env_file`` /
        ``env_file_encoding`` already resolved on ``dotenv_settings`` so
        that per-call overrides like ``Settings(_env_file="./other.env")``
        keep working — pydantic-settings bakes those overrides into the
        ``dotenv_settings`` instance it hands us here.

        The priority order (``init_settings`` first) is unchanged from
        pydantic-settings' default so programmatic overrides still win
        over env vars and dotenv values.
        """
        filtered_dotenv_source = FilteredDotEnvSettingsSource(
            settings_cls,
            env_file=dotenv_settings.env_file,  # type: ignore[attr-defined]
            env_file_encoding=dotenv_settings.env_file_encoding,  # type: ignore[attr-defined]
        )
        return (
            init_settings,
            env_settings,
            filtered_dotenv_source,
            file_secret_settings,
        )

    # ``Literal`` narrowing (issue #370) forces Pydantic to reject typoed
    # values such as ``APP_ENV=Production`` (wrong case) or ``APP_ENV=prod``
    # at ``Settings()`` construction. Previously the field was a free-form
    # ``str``, so a typoed shell env var silently landed in a non-production
    # branch — and ``main.py`` uses ``app_env == "production"`` to disable
    # ``/docs``, ``/redoc``, and ``/openapi.json`` in production, so a typo
    # exposed the interactive docs UI in a live deploy. Pydantic's
    # ``Literal`` matching is case-sensitive by design, which is what we
    # want here: "Production" is not the same as "production".
    app_env: Literal["development", "production", "testing"] = "development"
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
    # Whether the CORS middleware returns ``Access-Control-Allow-Credentials:
    # true``. Defaults to ``False`` because this service has no cookie or
    # session authentication — opting in is extra attack surface for no
    # functional gain. Additionally, the CORS spec forbids combining
    # ``allow_credentials=True`` with ``Access-Control-Allow-Origin: *``;
    # ``_reject_credentials_with_wildcard_origin`` below enforces that
    # invariant at config-load time so the failure is loud rather than
    # Starlette silently dropping credentials in that configuration.
    # Issue #346.
    cors_allow_credentials: bool = False

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

    # ``repr=False`` so authenticated-proxy URLs like
    # ``http://user:pass@proxy:11434`` never leak into ``repr(settings)``
    # (exception tracebacks, debug dumps, structlog payloads that fold
    # the full settings object). Pydantic omits the field entirely from
    # the model ``repr`` — both name and value — when ``repr=False``.
    # ``Settings.log_redacted_keys`` is the complementary guard for the
    # structlog redaction-filter path (it scrubs logger event kwargs by
    # key name) and does not reach Pydantic field reprs — this is the
    # model-level guard for issue #284.
    ollama_base_url: str = Field(
        default="http://host.docker.internal:11434",
        repr=False,
    )
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

    @model_validator(mode="after")
    def _reject_credentials_with_wildcard_origin(self) -> Self:
        """Reject ``cors_allow_credentials=True`` combined with wildcard origin.

        The CORS spec forbids returning ``Access-Control-Allow-Credentials:
        true`` together with ``Access-Control-Allow-Origin: *``. Starlette's
        ``CORSMiddleware`` silently drops credentials in that configuration,
        which masks the misconfiguration — and the combination is a
        well-known CVE pattern (credentials leaking to any origin). Raising
        at config-load time surfaces the problem loudly so the operator
        narrows ``cors_origins`` to an explicit allowlist before the
        service boots. Issue #346.
        """
        if self.cors_allow_credentials and "*" in self.cors_origins:
            msg = (
                "cors_allow_credentials=True is incompatible with a wildcard "
                "cors_origins entry ('*'). The CORS spec forbids credentialed "
                "responses to wildcarded origins. Either scope cors_origins "
                "to an explicit allowlist, or set cors_allow_credentials=False."
            )
            raise ValueError(msg)
        return self

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

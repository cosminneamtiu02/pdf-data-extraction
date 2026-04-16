"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.health_router import router as health_router
from app.api.middleware import configure_middleware
from app.core.config import Settings
from app.core.logging import configure_logging
from app.exceptions import SkillValidationFailedError
from app.features.extraction.router import router as extraction_router
from app.features.extraction.skills import (
    SkillDoclingConfig,
    SkillLoader,
    SkillManifest,
)

_logger = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    try:
        yield
    finally:
        # Only close the provider if something actually constructed one during
        # the app's lifetime. The `get_intelligence_provider` dep lazily
        # builds and caches it on `app.state`, so presence of the attribute
        # is the signal — no need to instantiate an `httpx.AsyncClient` just
        # to close it immediately.
        probe = getattr(app.state, "ollama_health_probe", None)
        if probe is not None:
            await probe.aclose()
            del app.state.ollama_health_probe

        provider = getattr(app.state, "intelligence_provider", None)
        if provider is not None:
            await provider.aclose()
            del app.state.intelligence_provider


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Passing an explicit `settings` is the supported seam for integration tests
    that need a custom `skills_dir` (or other overrides) without mutating
    process-wide env vars. The resolved instance is stored on
    `application.state.settings` so `get_settings` (and every dependency
    that depends on it) reads it back through the FastAPI request path.
    """
    resolved_settings = settings or Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env

    configure_logging(
        log_level=resolved_settings.log_level,
        json_output=resolved_settings.app_env == "production",
        redacted_keys=resolved_settings.log_redacted_keys,
        max_value_length=resolved_settings.log_max_value_length,
    )

    is_prod = resolved_settings.app_env == "production"
    application = FastAPI(
        title="PDF Data Extraction API",
        description="Self-hosted PDF data extraction microservice",
        version="0.1.0",
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
        lifespan=_lifespan,
    )

    application.state.settings = resolved_settings

    configure_middleware(application, cors_origins=resolved_settings.cors_origins)
    register_exception_handlers(application)

    default_docling = SkillDoclingConfig(
        ocr=resolved_settings.docling_ocr_default,
        table_mode=resolved_settings.docling_table_mode_default,
    )
    loader = SkillLoader(default_docling=default_docling)
    try:
        loaded = loader.load(resolved_settings.skills_dir)
    except SkillValidationFailedError as exc:
        params = exc.params.model_dump() if exc.params else {}
        _logger.critical(
            "skill_validation_failed",
            file=params.get("file"),
            reason=params.get("reason"),
            exc_info=True,
        )
        raise
    application.state.skill_manifest = SkillManifest(loaded)

    application.include_router(health_router)
    # ExtractionService is a stub until PDFX-E006-F002 merges.  Requests
    # that reach the stub hit the catch-all handler and return 500
    # INTERNAL_ERROR, which is correct "not implemented yet" behaviour.
    # F002 MUST merge before (or together with) this branch.
    application.include_router(extraction_router, prefix="/api/v1")

    return application


app = create_app()

"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.deps import get_intelligence_provider, get_settings
from app.api.errors import register_exception_handlers
from app.api.health_router import router as health_router
from app.api.middleware import configure_middleware
from app.core.config import Settings
from app.core.logging import configure_logging
from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills import (
    SkillDoclingConfig,
    SkillLoader,
    SkillManifest,
)

_logger = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    try:
        yield
    finally:
        # Only close the provider if something actually constructed one during
        # the app's lifetime — touching `get_intelligence_provider()` here
        # otherwise instantiates an `httpx.AsyncClient` for the sole purpose
        # of immediately closing it.
        if get_intelligence_provider.cache_info().currsize > 0:
            await get_intelligence_provider().aclose()
            # Evict the now-closed provider so any subsequent `create_app()`
            # / lifespan in the same process (tests, factory reuse) builds a
            # fresh instance instead of handing out one with a dead client.
            get_intelligence_provider.cache_clear()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Passing an explicit `settings` bypasses the cached `get_settings()` factory
    and is the supported seam for integration tests that need a custom
    `skills_dir` (or other overrides) without mutating process-wide env vars.
    """
    resolved_settings = settings or get_settings()

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
        )
        raise
    application.state.skill_manifest = SkillManifest(loaded)

    application.include_router(health_router)

    return application


app = create_app()

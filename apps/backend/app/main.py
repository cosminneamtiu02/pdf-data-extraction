"""FastAPI application factory."""

from fastapi import FastAPI

from app.api.deps import get_settings
from app.api.errors import register_exception_handlers
from app.api.health_router import router as health_router
from app.api.middleware import configure_middleware
from app.core.config import Settings
from app.core.logging import configure_logging
from app.features.extraction.skills import (
    SkillDoclingConfig,
    SkillLoader,
    SkillManifest,
)


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
    )

    configure_middleware(application, cors_origins=resolved_settings.cors_origins)
    register_exception_handlers(application)

    default_docling = SkillDoclingConfig(
        ocr=resolved_settings.docling_ocr_default,
        table_mode=resolved_settings.docling_table_mode_default,
    )
    loader = SkillLoader(default_docling=default_docling)
    application.state.skill_manifest = SkillManifest(loader.load(resolved_settings.skills_dir))

    application.include_router(health_router)

    return application


app = create_app()

"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.health_router import router as health_router
from app.api.middleware import configure_middleware
from app.api.probe_cache import ProbeCache
from app.core.config import Settings
from app.core.logging import configure_logging
from app.exceptions import SkillValidationFailedError
from app.features.extraction.intelligence.ollama_gemma_provider import (
    build_tags_url,
)
from app.features.extraction.intelligence.ollama_health_probe import (
    OllamaHealthProbe,
)
from app.features.extraction.router import router as extraction_router
from app.features.extraction.skills import (
    SkillDoclingConfig,
    SkillLoader,
    SkillManifest,
)

_logger = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # --- Startup: build probe + cache eagerly, prime with initial result ---
    settings: Settings = app.state.settings

    probe = OllamaHealthProbe(
        tags_url=build_tags_url(settings.ollama_base_url),
        timeout_seconds=settings.ollama_probe_timeout_seconds,
    )
    app.state.ollama_health_probe = probe

    cache = ProbeCache(probe=probe, ttl_seconds=settings.ollama_probe_ttl_seconds)
    app.state.probe_cache = cache

    reachable = await probe.check()
    cache.prime(result=reachable)

    if reachable:
        _logger.info("ollama_reachable_at_startup")
    else:
        _logger.warning("ollama_unreachable_at_startup")

    try:
        yield
    finally:
        # Close each lazily-constructed resource independently. Each cleanup
        # is wrapped in its own try/except so a failure in one does not
        # prevent the others from running (e.g. probe.aclose() raising must
        # not skip provider.aclose()).
        #
        # The probe_cache is also cleared because it holds a reference to the
        # probe's (now-closed) httpx client. Without clearing it, a reused
        # app instance would serve a stale cache backed by a closed client.
        for attr in ("ollama_health_probe", "probe_cache", "intelligence_provider"):
            obj = getattr(app.state, attr, None)
            if obj is not None:
                try:
                    if hasattr(obj, "aclose"):
                        await obj.aclose()
                except Exception:  # noqa: BLE001 - cleanup must not prevent sibling resources from closing
                    _logger.warning("lifespan_cleanup_failed", attr=attr, exc_info=True)
                finally:
                    delattr(app.state, attr)


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
    application.include_router(extraction_router, prefix="/api/v1")

    return application


app = create_app()

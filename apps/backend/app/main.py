"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.deps import get_intelligence_provider, get_settings
from app.api.errors import register_exception_handlers
from app.api.health_router import router as health_router
from app.api.middleware import configure_middleware
from app.core.logging import configure_logging


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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    configure_logging(
        log_level=settings.log_level,
        json_output=settings.app_env == "production",
        redacted_keys=settings.log_redacted_keys,
        max_value_length=settings.log_max_value_length,
    )

    is_prod = settings.app_env == "production"
    application = FastAPI(
        title="PDF Data Extraction API",
        description="Self-hosted PDF data extraction microservice",
        version="0.1.0",
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
        lifespan=_lifespan,
    )

    configure_middleware(application, cors_origins=settings.cors_origins)
    register_exception_handlers(application)

    # Health/readiness at root, outside /api/v1/
    application.include_router(health_router)

    # Business routes under /api/v1/ are added by individual feature slices
    # as they are implemented (the extraction feature lands in PDFX-E006).

    return application


app = create_app()

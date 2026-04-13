"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api.deps import get_settings
from app.api.errors import register_exception_handlers
from app.api.health_router import router as health_router
from app.api.middleware import configure_middleware
from app.core.database import dispose_engine
from app.core.logging import configure_logging
from app.features.widget.router import router as widget_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan — startup and graceful shutdown."""
    yield
    # Shutdown: dispose engine and close all connections cleanly
    await dispose_engine()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    configure_logging(
        log_level=settings.log_level,
        json_output=settings.app_env == "production",
    )

    is_prod = settings.app_env == "production"
    application = FastAPI(
        title="Project Template API",
        description="Backend API for the project template",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )

    configure_middleware(application, cors_origins=settings.cors_origins)
    register_exception_handlers(application)

    # Health/readiness at root, outside /api/v1/
    application.include_router(health_router)

    # Business routes under /api/v1/
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(widget_router)
    application.include_router(api_v1)

    return application


app = create_app()

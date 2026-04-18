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
    # Respect a pre-existing probe on app.state (test seam: integration tests
    # pre-populate it with a FakeProbe for deterministic probe behaviour
    # without relying on host network state).  In production app.state has no
    # probe at this point, so the real one is always constructed here.
    settings: Settings = app.state.settings

    probe: OllamaHealthProbe = getattr(app.state, "ollama_health_probe", None) or OllamaHealthProbe(  # type: ignore[assignment]  # test seam allows FakeProbe
        tags_url=build_tags_url(settings.ollama_base_url),
        expected_model=settings.ollama_model,
        timeout_seconds=settings.ollama_probe_timeout_seconds,
    )
    app.state.ollama_health_probe = probe

    cache: ProbeCache = getattr(app.state, "probe_cache", None) or ProbeCache(
        probe=probe,
        ttl_seconds=settings.ollama_probe_ttl_seconds,
    )
    app.state.probe_cache = cache

    # ``probe.check()`` now returns readiness — reachable AND the configured
    # model tag is installed — so the startup log event reflects "ready"
    # rather than the older "reachable" wording which was misleading when
    # Ollama responded 200 but was missing the pinned model (issue #107).
    #
    # The probe's own ``check()`` catches ``httpx.HTTPError`` and JSON decode
    # errors and returns ``False``, but any other unexpected ``Exception``
    # subclass (e.g. ``ValueError`` from a bad config, a ``DomainError``
    # raised deeper in the probe path, an ``AttributeError`` on a wrapped
    # client) would escape and crash the ASGI boot, causing the container
    # to crash-loop (issue #144). Degrading on startup is always preferable
    # to dying on startup: ``/health`` stays green and the cache is primed
    # ``False`` so the initial readiness state degrades cleanly instead of
    # aborting app startup. Runtime TTL refreshes are guarded separately
    # inside ``ProbeCache.is_ready()`` with the same broad ``except
    # Exception`` strategy, which keeps ``/ready`` on the documented 503
    # ``ollama_unreachable`` contract across the full process lifetime even
    # if the underlying fault is persistent, and the self-healing TTL
    # refresh recovers once Ollama behaves. ``except Exception`` is
    # deliberate here: it is one of the rare places where catching any
    # unexpected non-``BaseException`` startup failure and degrading is the
    # correct failure mode. ``BaseException`` subclasses such as
    # ``asyncio.CancelledError`` (a ``BaseException`` since Python 3.8),
    # ``SystemExit``, and ``KeyboardInterrupt`` are intentionally not
    # swallowed so shutdown and termination signals still propagate.
    try:
        ready = await probe.check()
    except Exception as exc:  # noqa: BLE001 - degrade-don't-crash is the contract
        _logger.warning(
            "probe_check_failed_at_startup",
            error_class=type(exc).__name__,
            exc_info=True,
        )
        ready = False

    cache.prime(result=ready)

    if ready:
        _logger.info("ollama_ready_at_startup")
    else:
        _logger.warning("ollama_not_ready_at_startup")

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
        #
        # ``extraction_service`` and its collaborator caches
        # (``structured_output_validator``, ``document_parser``,
        # ``text_concatenator``, ``extraction_engine``, ``span_resolver``,
        # ``pdf_annotator``) are also cleared. ``extraction_service`` holds
        # the ``intelligence_provider`` internally; without invalidating it
        # on shutdown, a re-entered lifespan would rebuild a fresh provider
        # on ``app.state`` but ``get_extraction_service`` would still return
        # the first lifespan's cached service, whose ``_intelligence_provider``
        # points at the already-``aclose()``'d provider. The next extraction
        # request then fails with a closed-httpx-client 500. The sibling
        # component caches are invalidated for the same reason: the
        # re-entered lifespan must build a fresh graph so every dependency
        # reflects the current ``app.state.settings`` and current
        # ``Depends()`` override map, not values captured at the first
        # lifespan's first-resolve.
        for attr in (
            "ollama_health_probe",
            "probe_cache",
            "intelligence_provider",
            "extraction_service",
            "structured_output_validator",
            "document_parser",
            "text_concatenator",
            "extraction_engine",
            "span_resolver",
            "pdf_annotator",
        ):
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

    configure_middleware(
        application,
        cors_origins=resolved_settings.cors_origins,
        max_upload_bytes=resolved_settings.max_pdf_bytes,
        cors_methods=resolved_settings.cors_methods,
        cors_headers=resolved_settings.cors_headers,
    )
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

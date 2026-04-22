"""FastAPI application factory."""

import contextlib
import inspect
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast

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

if TYPE_CHECKING:
    from types import SimpleNamespace

    from starlette.datastructures import State

_logger = structlog.get_logger(__name__)


# Attributes set by ``create_app`` on ``app.state`` that must survive
# lifespan re-enters. These are process-scoped (bound once at app
# construction) and carry no open network/file/thread handles —
# keeping them across lifespan boundaries is correct because the
# second lifespan is expected to read the same configuration the first
# was built against.
#
# Every OTHER attribute on ``app.state`` is treated as lifespan-scoped
# and cleaned up by ``_lifespan_cleanup`` on shutdown, regardless of
# whether it was pre-installed as a test seam or lazily cached during
# a request. This replaces the issue-#381 hardcoded 10-entry cleanup
# tuple with a single allowlist of long-lived attrs — a much smaller,
# more stable invariant to maintain.
#
# A cached attribute added here by accident would leak resources on
# shutdown; an attribute that belongs here but is forgotten would
# simply be re-created next lifespan (slightly wasteful but not
# incorrect). The former is the more dangerous failure mode, which is
# why this set is kept minimal and documented.
_LIFESPAN_PRESERVED_ATTRS: frozenset[str] = frozenset(
    {
        "settings",
        "skill_manifest",
    },
)


async def _lifespan_cleanup(
    state: "State | SimpleNamespace",
    preserved_attrs: frozenset[str] = _LIFESPAN_PRESERVED_ATTRS,
) -> None:
    """Clean up every ``app.state`` attribute not in ``preserved_attrs``.

    Issue #381: the old cleanup block hardcoded a 10-entry tuple of
    attribute names. Every new lazily-cached dependency added to
    ``app/api/deps.py`` or ``app/features/extraction/deps.py`` had to be
    kept in sync with that tuple manually — a footgun with no enforcement.
    The architecture test in
    ``tests/unit/architecture/test_lifespan_cleanup_ast.py`` is a second
    layer of defense but the primary fix is this generic helper: we
    iterate over everything on ``app.state`` that is not in the small
    ``_LIFESPAN_PRESERVED_ATTRS`` allowlist, await ``aclose()`` on
    objects that expose it, and always ``delattr`` so the re-entered
    lifespan rebuilds a fresh DI graph.

    Failures on individual ``aclose`` calls are logged via structlog
    under the ``lifespan_cleanup_failed`` event and never propagated:
    shutdown must be best-effort so a failure in one resource does not
    prevent sibling resources from closing. CLAUDE.md forbids silently
    swallowing exceptions, so every failure is logged — it is not
    swallowed.
    """
    # Starlette's `State` proxies attribute access through `self._state` (a
    # plain dict); `vars(state)` on a real request.app.state returns only
    # `{"_state": dict}` — the storage slot itself, not the user attributes.
    # Iterating `vars(state)` and calling `delattr` would wipe starlette's
    # internal dict and break every subsequent lookup. Read `state._state`
    # when present (production path) and fall back to `vars(state)` for
    # SimpleNamespace-style test fakes. The extra `_state` filter is
    # defensive against future introspection shapes.
    underlying_raw: object = getattr(state, "_state", None)
    underlying: dict[str, object]
    if isinstance(underlying_raw, dict):
        underlying = cast("dict[str, object]", underlying_raw)
    else:
        underlying = cast("dict[str, object]", vars(state))
    state_attrs: dict[str, object] = dict(underlying)
    to_clean: list[tuple[str, object]] = [
        (name, value)
        for name, value in state_attrs.items()
        if name not in preserved_attrs and name != "_state"
    ]

    for attr_name, obj in to_clean:
        aclose = getattr(obj, "aclose", None)
        try:
            if callable(aclose):
                result = aclose()
                if inspect.isawaitable(result):
                    await result
        except Exception as exc:  # noqa: BLE001 - cleanup must not prevent sibling resources from closing
            _logger.warning(
                "lifespan_cleanup_failed",
                attr=attr_name,
                error_class=type(exc).__name__,
                exc_info=True,
            )
        finally:
            # Always delattr so the re-entered lifespan resolves fresh
            # dependencies through the Depends() graph rather than
            # handing back stale cached objects (integration test
            # ``test_lifespan_service_cache`` pins this invariant at
            # the transport layer). ``contextlib.suppress`` covers the
            # defensive case where a nested ``aclose`` reached into
            # ``app.state`` and already removed the attribute.
            with contextlib.suppress(AttributeError):
                delattr(state, attr_name)


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
        # Generic cleanup (issue #381): previously this block hardcoded a
        # 10-entry tuple of attribute names that had to be kept in sync
        # every time a new lazily-cached dependency was added in
        # ``app/api/deps.py`` or ``app/features/extraction/deps.py``.
        # A forgotten entry leaked resources on lifespan re-enter.
        #
        # The helper walks ``vars(app.state)`` and cleans up every attr
        # that is not in ``_LIFESPAN_PRESERVED_ATTRS`` (``settings``,
        # ``skill_manifest`` — the process-scoped attrs ``create_app``
        # sets before the lifespan runs). This keeps the probe +
        # cache + every DI-cached pipeline component in scope without
        # naming them individually, so the next lazily-cached dep is
        # covered automatically.
        #
        # The extraction service and its collaborator caches are cleared
        # for the same reason the old code cleared them: a re-entered
        # lifespan must build a fresh DI graph so every dependency
        # reflects the current ``app.state.settings`` and current
        # ``Depends()`` override map, not values captured at the first
        # lifespan's first-resolve.
        await _lifespan_cleanup(app.state)


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
        cors_allow_credentials=resolved_settings.cors_allow_credentials,
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

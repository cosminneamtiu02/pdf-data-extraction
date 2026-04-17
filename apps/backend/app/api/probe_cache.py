"""TTL-cached readiness probe result.

Single-entry in-process cache. The ``/ready`` handler queries this; if the
cached result is still within the TTL window it is returned immediately
(O(1), no network call). Otherwise the underlying ``OllamaHealthProbe`` is
called, the result is stored, and the timestamp is updated.

An ``asyncio.Lock`` guards the refresh path so concurrent ``/ready`` calls
at TTL expiry coalesce into a single probe instead of a thundering herd.

The cache supports *priming* (``prime()``) so the startup sequence can seed
it with the initial probe result. When a probe refresh flips the cached
state from ``False`` to ``True``, an ``ollama_reachable_recovered`` event
is logged at INFO level (PDFX-E007-F002 self-healing).  The reverse
transition (``True`` → ``False``) logs ``ollama_became_unreachable`` at
WARNING so operators see the degradation at normal log levels.

Exception handling during refresh: ``probe.check()`` is wrapped in a
broad ``except Exception`` inside ``is_ready()`` so any non-httpx
exception (bad config, wrapped-client attribute errors, unexpected
``DomainError`` subclasses) is converted into cached-``False`` plus a
``probe_check_failed_on_refresh`` WARNING. This mirrors the startup
guard in ``app/main.py._lifespan`` (issue #144) and keeps ``/ready``
on the documented 503 ``ollama_unreachable`` contract across the
entire process lifetime rather than only within the primed TTL window.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from app.features.extraction.intelligence.ollama_health_probe import (
        OllamaHealthProbe,
    )

_logger = structlog.get_logger(__name__)


class ProbeCache:
    """TTL-gated cache around an ``OllamaHealthProbe``."""

    def __init__(
        self,
        *,
        probe: OllamaHealthProbe,
        ttl_seconds: float,
    ) -> None:
        self._probe = probe
        self._ttl = ttl_seconds
        self._last_check_time: float = 0.0
        self._last_result: bool = False
        # Distinguishes "never checked" from "checked and got False".
        # Guards the recovery-logging condition and the TTL fast-path:
        # the cache is only considered populated when this is True.
        self._has_previous_result: bool = False
        # asyncio.Lock() is loop-free at construction since Python 3.10;
        # it binds to the running loop on first ``await``. Safe to create
        # here even though the DI factory (``get_probe_cache``) is sync.
        self._lock = asyncio.Lock()

    def prime(self, *, result: bool) -> None:
        """Seed the cache with a startup probe result.

        Called once during lifespan startup so ``/ready`` returns the
        correct state immediately without waiting for the first request
        to trigger a lazy probe.
        """
        self._last_result = result
        self._last_check_time = time.monotonic()
        self._has_previous_result = True

    async def is_ready(self) -> bool:
        """Return cached probe result, refreshing if the TTL has expired."""
        now = time.monotonic()
        if self._has_previous_result and (now - self._last_check_time) < self._ttl:
            return self._last_result
        async with self._lock:
            # Re-check inside the lock: another coroutine may have refreshed
            # the cache while we were waiting for the lock.
            now = time.monotonic()
            if self._has_previous_result and (now - self._last_check_time) < self._ttl:
                return self._last_result
            previous = self._last_result
            had_previous = self._has_previous_result
            # ``probe.check()`` catches ``httpx.HTTPError`` and JSON decode
            # errors internally and returns ``False``, but any other
            # exception (e.g. ``ValueError`` from a bad config, an
            # ``AttributeError`` on a wrapped client, a ``DomainError``
            # subclass raised deeper in the probe path) would escape and
            # turn the ``/ready`` endpoint into a 500. The lifespan's
            # startup guard (issue #144) only covers the startup probe;
            # this mirror-guard on every TTL refresh keeps the ``/ready``
            # contract stable (503 ``ollama_unreachable``) for the full
            # lifetime of the process, not just the primed-TTL window.
            # ``except Exception`` is deliberate here for the same reason
            # as in ``app/main.py._lifespan``: degrade rather than crash.
            try:
                refreshed = await self._probe.check()
            except Exception as exc:  # noqa: BLE001 - degrade-don't-crash is the contract
                _logger.warning(
                    "probe_check_failed_on_refresh",
                    error_class=type(exc).__name__,
                    exc_info=True,
                )
                refreshed = False
            self._last_result = refreshed
            self._last_check_time = time.monotonic()
            self._has_previous_result = True
            if had_previous and not previous and self._last_result:
                _logger.info("ollama_reachable_recovered")
            elif had_previous and previous and not self._last_result:
                _logger.warning("ollama_became_unreachable")
            return self._last_result

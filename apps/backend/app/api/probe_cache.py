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
is logged at INFO level (PDFX-E007-F002 self-healing).
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
        if self._last_check_time > 0 and (now - self._last_check_time) < self._ttl:
            return self._last_result
        async with self._lock:
            # Re-check inside the lock: another coroutine may have refreshed
            # the cache while we were waiting for the lock.
            now = time.monotonic()
            if self._last_check_time > 0 and (now - self._last_check_time) < self._ttl:
                return self._last_result
            previous = self._last_result
            had_previous = self._has_previous_result
            self._last_result = await self._probe.check()
            self._last_check_time = time.monotonic()
            self._has_previous_result = True
            if had_previous and not previous and self._last_result:
                _logger.info("ollama_reachable_recovered")
            return self._last_result

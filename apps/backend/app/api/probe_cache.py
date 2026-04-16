"""TTL-cached readiness probe result.

Single-entry in-process cache. The ``/ready`` handler queries this; if the
cached result is still within the TTL window it is returned immediately
(O(1), no network call). Otherwise the underlying ``OllamaHealthProbe`` is
called, the result is stored, and the timestamp is updated.

An ``asyncio.Lock`` guards the refresh path so concurrent ``/ready`` calls
at TTL expiry coalesce into a single probe instead of a thundering herd.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.features.extraction.intelligence.ollama_health_probe import (
        OllamaHealthProbe,
    )


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
        self._lock = asyncio.Lock()

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
            self._last_result = await self._probe.check()
            self._last_check_time = time.monotonic()
            return self._last_result

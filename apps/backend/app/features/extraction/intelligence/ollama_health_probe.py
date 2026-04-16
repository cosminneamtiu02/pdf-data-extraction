"""Ollama readiness probe for the /ready endpoint.

Pings Ollama's ``/api/tags`` endpoint and returns a boolean signal.
This is the second file (alongside ``ollama_gemma_provider.py``) that is
pre-authorized to import ``httpx`` within the extraction feature — see
the C6 httpx-containment contract in ``import-linter-contracts.ini``.
"""

from __future__ import annotations

import httpx
import structlog

_logger = structlog.get_logger(__name__)

_DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0


class OllamaHealthProbe:
    """Lightweight probe that checks Ollama reachability.

    Constructed with the full tags URL (built by the caller in the DI
    factory) and an optional pre-built ``httpx.AsyncClient`` (test seam).
    If no client is provided, one is created with a short timeout so a
    hung Ollama does not back up the readiness check.
    """

    def __init__(
        self,
        *,
        tags_url: str,
        timeout_seconds: float = _DEFAULT_PROBE_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._tags_url = tags_url
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def check(self) -> bool:
        """Ping ``/api/tags``. Return True on 200, False on any failure."""
        try:
            response = await self._http_client.get(self._tags_url)
            response.raise_for_status()
        except httpx.HTTPError:
            _logger.debug("ollama_probe_failed", url=self._tags_url, exc_info=True)
            return False
        return True

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http_client.aclose()

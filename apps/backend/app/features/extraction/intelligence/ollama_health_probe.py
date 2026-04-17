"""Ollama readiness probe for the /ready endpoint.

Pings Ollama's ``/api/tags`` endpoint and returns a boolean signal.
A 200 response alone is not enough: the probe also inspects the returned
``models`` list and only reports ready when the configured
``Settings.ollama_model`` tag appears in it. A reachable Ollama that is
missing the pinned model will produce extraction failures on every
request, so the service is not truly ready.

This is the second file (alongside ``ollama_gemma_provider.py``) that is
pre-authorized to import ``httpx`` within the extraction feature — see
the C6 httpx-containment contract in ``import-linter-contracts.ini``.
"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import structlog

_logger = structlog.get_logger(__name__)

_DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0


class OllamaHealthProbe:
    """Lightweight probe that checks Ollama reachability and model availability.

    Constructed with the full tags URL (built by the caller in the DI
    factory), the configured model tag to look for, and an optional
    pre-built ``httpx.AsyncClient`` (test seam). If no client is
    provided, one is created with a short timeout so a hung Ollama does
    not back up the readiness check.
    """

    def __init__(
        self,
        *,
        tags_url: str,
        expected_model: str,
        timeout_seconds: float = _DEFAULT_PROBE_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._tags_url = tags_url
        self._expected_model = expected_model
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def check(self) -> bool:
        """Ping ``/api/tags`` and confirm the configured model is installed.

        Returns ``True`` only when Ollama responds 200 AND the returned
        ``models`` list contains an entry whose ``name`` matches
        ``expected_model``. Any HTTP failure, decode error, unexpected
        response shape, or missing model yields ``False``.
        """
        try:
            response = await self._http_client.get(self._tags_url)
            response.raise_for_status()
        except httpx.HTTPError:
            _logger.debug("ollama_probe_failed", url=self._tags_url, exc_info=True)
            return False

        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            _logger.warning(
                "ollama_probe_invalid_json",
                url=self._tags_url,
                exc_info=True,
            )
            return False

        installed = _extract_model_names(body)
        if self._expected_model in installed:
            return True

        _logger.warning(
            "ollama_model_not_found",
            url=self._tags_url,
            status_code=response.status_code,
            expected_model=self._expected_model,
            installed_models=installed,
        )
        return False

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http_client.aclose()


def _extract_model_names(body: object) -> list[str]:
    """Return the list of model ``name`` values from an ``/api/tags`` body.

    Defensive against unexpected shapes: returns an empty list for any
    body that is not the documented ``{"models": [{"name": str, ...}, ...]}``
    structure. The caller treats "no names" the same as "expected model
    missing," so the probe fails closed on unfamiliar payloads.

    Takes ``object`` rather than ``Any`` so ``isinstance(body, dict)``
    narrows correctly under pyright strict. After each narrowing we
    ``cast`` to the project-wide ``dict[str, Any]`` / ``list[Any]`` shape
    used by ``ollama_gemma_provider.py``, keeping JSON-decoded access
    untyped but consolidated into a single cast per layer — which pyright
    accepts without ``type: ignore`` suppressions.
    """
    if not isinstance(body, dict):
        return []
    body_dict = cast("dict[str, Any]", body)
    models = body_dict.get("models")
    if not isinstance(models, list):
        return []
    models_list = cast("list[Any]", models)
    names: list[str] = []
    for entry in models_list:
        if isinstance(entry, dict):
            entry_dict = cast("dict[str, Any]", entry)
            name = entry_dict.get("name")
            if isinstance(name, str):
                names.append(name)
    return names

"""OllamaGemmaProvider: dual-interface Ollama HTTP client.

One class, two conformances. Satisfies the internal `IntelligenceProvider`
protocol via `async generate(prompt, output_schema) -> GenerationResult`, and
simultaneously conforms to LangExtract's community provider plugin contract by
inheriting from `langextract.core.base_model.BaseLanguageModel` and registering
itself via `langextract.providers.registry.register` on a regex matching Gemma
model IDs. The same registered entry point is declared in `pyproject.toml` so
that fresh LangExtract processes discover the provider via Python's
`importlib.metadata` entry-points mechanism.

Containment: this is the ONE file in `apps/backend/app/features/extraction/`
that imports `httpx`. Every other piece of HTTP-to-Ollama knowledge — the URL
shape `/api/generate`, the `/api/tags` health probe path, the request payload
schema — lives here and nowhere else. Import-linter contracts land in
PDFX-E007-F004; until then, two AST-scan unit tests enforce the invariant.

Sync/async bridge: LangExtract's orchestration is synchronous, and its
`BaseLanguageModel.infer` is a sync generator. The provider bridges to our
async HTTP path via `asyncio.run` inside `infer`. This is safe because
`ExtractionEngine` (PDFX-E004-F003) will call LangExtract from
`asyncio.to_thread`, which gives `infer` a fresh thread with no running loop.
If `infer` is ever called directly from an async context, `asyncio.run` raises
`RuntimeError("This event loop is already running")` — the right outcome,
because it surfaces the incorrect call site instead of deadlocking.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from langextract.core.base_model import BaseLanguageModel
from langextract.core.types import ScoredOutput
from langextract.providers.router import register

from app.features.extraction.intelligence.intelligence_unavailable_error import (
    IntelligenceUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from app.core.config import Settings
    from app.features.extraction.intelligence.generation_result import GenerationResult
    from app.features.extraction.intelligence.structured_output_validator import (
        StructuredOutputValidator,
    )

_CONNECT_ERROR_MESSAGE = "Ollama connection failed"
_TIMEOUT_MESSAGE = "Ollama request timed out"
_HTTP_ERROR_MESSAGE_TEMPLATE = "Ollama returned HTTP {status}"
_MISSING_RESPONSE_FIELD_MESSAGE = "Ollama response body missing 'response' string field"

_logger = structlog.get_logger(__name__)


def _build_generate_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/generate"


def _build_tags_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/tags"


def _build_payload(model: str, prompt: str) -> dict[str, Any]:
    return {"model": model, "prompt": prompt, "stream": False}


@register(r"^gemma", priority=20)
class OllamaGemmaProvider(BaseLanguageModel):
    def __init__(
        self,
        settings: Settings,
        validator: StructuredOutputValidator,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__()
        self._model = settings.ollama_model
        self._generate_url = _build_generate_url(settings.ollama_base_url)
        self._tags_url = _build_tags_url(settings.ollama_base_url)
        self._validator = validator
        self.http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ollama_timeout_seconds),
        )

    async def generate(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> GenerationResult:
        raw_text = await self._raw_generate(prompt)

        async def _regenerate(correction_prompt: str) -> str:
            return await self._raw_generate(correction_prompt)

        return await self._validator.validate_and_retry(
            raw_text,
            output_schema,
            _regenerate,
            original_prompt=prompt,
        )

    async def _raw_generate(self, prompt: str) -> str:
        payload = _build_payload(self._model, prompt)
        try:
            response = await self.http_client.post(self._generate_url, json=payload)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            _logger.warning("ollama_connect_error", error=str(exc))
            message = _CONNECT_ERROR_MESSAGE
            raise IntelligenceUnavailableError(message, cause=exc) from exc
        except httpx.TimeoutException as exc:
            _logger.warning("ollama_timeout", error=str(exc))
            message = _TIMEOUT_MESSAGE
            raise IntelligenceUnavailableError(message, cause=exc) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            _logger.warning("ollama_http_error", status=status)
            message = _HTTP_ERROR_MESSAGE_TEMPLATE.format(status=status)
            raise IntelligenceUnavailableError(message, cause=exc) from exc

        body: dict[str, Any] = response.json()
        response_text = body.get("response")
        if not isinstance(response_text, str):
            message = _MISSING_RESPONSE_FIELD_MESSAGE
            raise IntelligenceUnavailableError(message)
        return response_text

    def infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,  # noqa: ARG002 - LangExtract passes orchestrator kwargs that we do not consume
    ) -> Iterator[Sequence[ScoredOutput]]:
        for prompt in batch_prompts:
            raw = asyncio.run(self._raw_generate(prompt))
            yield [ScoredOutput(score=1.0, output=raw)]

    async def health_check(self) -> bool:
        try:
            response = await self.http_client.get(self._tags_url)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
            return False
        return True

    async def aclose(self) -> None:
        await self.http_client.aclose()

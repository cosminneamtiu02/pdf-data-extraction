"""OllamaGemmaProvider: dual-interface Ollama HTTP client.

One class, two conformances. Satisfies the internal `IntelligenceProvider`
protocol via `async generate(prompt, output_schema) -> GenerationResult`, and
simultaneously conforms to LangExtract's community provider plugin contract by
inheriting from `langextract.core.base_model.BaseLanguageModel` and registering
itself via `langextract.providers.router.register` on a regex matching Gemma
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
import json
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog
from langextract.core.base_model import BaseLanguageModel
from langextract.core.types import ScoredOutput
from langextract.providers.router import register

from app.core.config import Settings
from app.exceptions import IntelligenceUnavailableError
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.langextract_wrapper_schema import (
    LANGEXTRACT_WRAPPER_SCHEMA,
)
from app.features.extraction.intelligence.structured_output_validator import (
    StructuredOutputValidator,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from app.features.extraction.intelligence.generation_result import GenerationResult

_logger = structlog.get_logger(__name__)

# HTTP status class boundaries. Extracted as module constants so the 4xx/5xx
# discriminator in `_raw_generate` is not flagged as a magic number and so the
# intent ("client error range", "server error range") is self-documenting at
# the call site.
_HTTP_CLIENT_ERROR_MIN = 400
_HTTP_SERVER_ERROR_MIN = 500


def _build_generate_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/generate"


def _build_tags_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/tags"


def _build_payload(model: str, prompt: str) -> dict[str, Any]:
    return {"model": model, "prompt": prompt, "stream": False}


@register(r"^gemma", priority=20)
class OllamaGemmaProvider(BaseLanguageModel):
    """Dual-interface Ollama provider.

    Two construction paths:

    1. FastAPI `Depends()` factory path — `OllamaGemmaProvider(settings=...,
       validator=...)`. Used by `app.api.deps.get_intelligence_provider`. All
       config comes from the injected `Settings` instance.
    2. LangExtract plugin path — `OllamaGemmaProvider(model_id=<tag>,
       **langextract_kwargs)`. `langextract.factory.create_model` calls
       `provider_class(**kwargs)` where `kwargs["model_id"]` is the model tag.
       When this path fires, we lazily construct a default `Settings()` and a
       matching `StructuredOutputValidator`, honoring `model_id` as an
       override of `settings.ollama_model`. Any extra kwargs LangExtract
       passes (`model_url`, `timeout`, `format_type`, `constraint`, …) are
       absorbed and ignored — Ollama/LangExtract's own concerns are not the
       provider's to re-implement here.
    """

    def __init__(
        self,
        model_id: str | None = None,
        *,
        settings: Settings | None = None,
        validator: StructuredOutputValidator | None = None,
        http_client: httpx.AsyncClient | None = None,
        **_langextract_kwargs: Any,
    ) -> None:
        super().__init__()
        effective_settings = settings if settings is not None else Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env
        effective_validator = (
            validator
            if validator is not None
            else StructuredOutputValidator(
                settings=effective_settings,
                correction_prompt_builder=CorrectionPromptBuilder(),
            )
        )
        self._model = model_id or effective_settings.ollama_model
        self._generate_url = _build_generate_url(effective_settings.ollama_base_url)
        self._tags_url = _build_tags_url(effective_settings.ollama_base_url)
        self._validator = effective_validator
        self.http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(effective_settings.ollama_timeout_seconds),
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
            _logger.warning("intelligence_unavailable", cause="connect_error", error=str(exc))
            raise IntelligenceUnavailableError from exc
        except httpx.TimeoutException as exc:
            _logger.warning("intelligence_unavailable", cause="timeout", error=str(exc))
            raise IntelligenceUnavailableError from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            cause = (
                "http_4xx"
                if _HTTP_CLIENT_ERROR_MIN <= status < _HTTP_SERVER_ERROR_MIN
                else "http_5xx"
            )
            _logger.warning("intelligence_unavailable", cause=cause, status=status)
            raise IntelligenceUnavailableError from exc

        try:
            decoded: Any = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            # Ollama (or an interposing proxy) returned a non-JSON body. Treat
            # the same as unreachability — operators reading the log see the
            # underlying decode error on the log line's `error` field.
            _logger.warning("intelligence_unavailable", cause="non_json_body", error=str(exc))
            raise IntelligenceUnavailableError from exc
        if not isinstance(decoded, dict):
            # httpx decodes any valid JSON root, including lists, strings,
            # and numbers. A non-object root cannot carry Ollama's `response`
            # field, so short-circuit here rather than blowing up downstream
            # with an AttributeError on `.get("response")`.
            _logger.warning(
                "intelligence_unavailable",
                cause="invalid_json_shape",
                shape=type(decoded).__name__,
            )
            raise IntelligenceUnavailableError from None
        # Pyright's isinstance narrowing gives us `dict[Unknown, Unknown]`;
        # cast to the shape Ollama's contract promises. Runtime keys are
        # already guaranteed str because JSON object keys are always strings.
        # The string-literal form of the type expression is the project-wide
        # convention for `cast` calls, enforced by ruff's `TC006`. Pyright
        # parses it identically to the unquoted form, so static checking is
        # not weakened — only the runtime cost of evaluating the type
        # expression is avoided.
        body = cast("dict[str, Any]", decoded)
        response_text = body.get("response")
        if not isinstance(response_text, str):
            _logger.warning("intelligence_unavailable", cause="missing_response_field")
            raise IntelligenceUnavailableError from None
        return response_text

    async def _validated_generate_batch(self, batch_prompts: Sequence[str]) -> list[str]:
        # Every prompt runs inside the SAME event loop so the shared
        # `httpx.AsyncClient` binds its connection pool to one loop only. If
        # `infer` called `asyncio.run` per prompt, the second prompt would hit
        # a client whose pool is bound to a closed loop. Each prompt routes
        # through `self.generate`, which runs the `StructuredOutputValidator`
        # fence-strip + JSON-parse + retry loop against the LangExtract
        # wrapper schema — so the plugin entry path enforces the same
        # CLAUDE.md-mandated "no bypass" invariant as the `generate()` path
        # the `_ValidatingLangExtractAdapter` in `extraction_engine.py` uses.
        outputs: list[str] = []
        for prompt in batch_prompts:
            result = await self.generate(prompt, LANGEXTRACT_WRAPPER_SCHEMA)
            outputs.append(json.dumps(result.data))
        return outputs

    def infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,  # noqa: ARG002 - LangExtract passes orchestrator kwargs that we do not consume
    ) -> Iterator[Sequence[ScoredOutput]]:
        validated_outputs = asyncio.run(self._validated_generate_batch(batch_prompts))
        for output in validated_outputs:
            yield [ScoredOutput(score=1.0, output=output)]

    async def health_check(self) -> bool:
        try:
            response = await self.http_client.get(self._tags_url)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
            return False
        return True

    async def aclose(self) -> None:
        await self.http_client.aclose()

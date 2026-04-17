"""OllamaGemmaProvider: dual-interface Ollama HTTP client.

One class, two conformances. Satisfies the internal `IntelligenceProvider`
protocol via `async generate(prompt, output_schema) -> GenerationResult`, and
simultaneously conforms to LangExtract's community provider plugin contract by
inheriting from `langextract.core.base_model.BaseLanguageModel` and registering
itself via `langextract.providers.router.register` on a regex matching Gemma
model IDs. The same registered entry point is declared in `pyproject.toml` so
that fresh LangExtract processes discover the provider via Python's
`importlib.metadata` entry-points mechanism.

Containment: this file and ``ollama_health_probe.py`` are the only files in
``apps/backend/app/features/extraction/`` that import ``httpx`` — authorized
by the C6 httpx-containment contract in ``import-linter-contracts.ini``.
The URL builder ``build_tags_url`` lives here and is reused by the probe's
DI factory so the URL shape is defined once.

Sync/async bridge: LangExtract's orchestration is synchronous, and its
`BaseLanguageModel.infer` is a sync generator. The provider bridges to our
async HTTP path via `asyncio.run` inside `infer`. This is safe because
`ExtractionEngine` (PDFX-E004-F003) will call LangExtract from
`asyncio.to_thread`, which gives `infer` a fresh thread with no running loop.
If `infer` is ever called directly from an async context, `asyncio.run` raises
`RuntimeError("This event loop is already running")` — the right outcome,
because it surfaces the incorrect call site instead of deadlocking.

Each ``infer()`` call creates a fresh ``httpx.AsyncClient`` inside the
``asyncio.run`` scope rather than reusing the instance-level client (issue #47).
``asyncio.run`` closes the event loop on return, and httpx binds its connection
pool to the loop on first use; reusing the instance client across two
``asyncio.run`` calls would raise ``RuntimeError: Event loop is closed`` on the
second invocation. The ``generate()`` and ``health_check()`` async paths now
also rebuild ``self.http_client`` when the running event loop changes — the
provider is safe to reuse across ``asyncio.run`` scopes from sync callers
(issue #132). Within one loop, the cached client is retained so connection
pooling still works.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog
from langextract.core.base_model import BaseLanguageModel
from langextract.core.types import ScoredOutput
from langextract.providers.router import register

from app.core.config import Settings
from app.exceptions import IntelligenceTimeoutError, IntelligenceUnavailableError
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


def build_tags_url(base_url: str) -> str:
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
        self._tags_url = build_tags_url(effective_settings.ollama_base_url)
        # ``Settings`` enforces ``ollama_timeout_seconds > 0``; keep the float
        # on the instance so the ``IntelligenceTimeoutError`` budget and the
        # ``intelligence_timeout`` log event can reference an unambiguous source
        # instead of reconstructing it from ``httpx.Timeout`` attributes.
        self._timeout_seconds = effective_settings.ollama_timeout_seconds
        self._timeout = httpx.Timeout(self._timeout_seconds)
        self._validator = effective_validator
        # `http_client` is eagerly created (matching the long-standing contract
        # that `.http_client` is a plain attribute) OR taken from `http_client`
        # if injected. `_get_http_client` rebuilds it when the running event
        # loop changes, so reusing a single provider across `asyncio.run`
        # scopes no longer trips `RuntimeError: Event loop is closed` (issue
        # #132). The rebuild ONLY fires inside async methods where a real
        # running loop exists — plain `.http_client` reads from sync code
        # (e.g. lifespan post-shutdown test assertions) never rebuild.
        self._injected_http_client = http_client
        self.http_client = http_client or httpx.AsyncClient(timeout=self._timeout)
        self._http_client_loop: asyncio.AbstractEventLoop | None = None

    def _get_http_client(self) -> httpx.AsyncClient:
        """Return an `AsyncClient` bound to the currently-running event loop.

        If the caller injected a client via the constructor, return it
        unchanged — they own its loop affinity. Otherwise rebuild
        `self.http_client` when the running loop differs from the one the
        current client was first used on. This fixes the cross-loop
        regression in issue #132 where a single provider instance used
        across `asyncio.run` scopes would raise `RuntimeError: Event loop
        is closed` on the second call.

        Old-client cleanup: when we rebind, the outgoing client is bound to
        the previous loop. If that loop is still running we schedule
        ``aclose()`` onto it via ``run_coroutine_threadsafe`` so sockets
        close cleanly; if it is already closed we cannot run any coroutine
        on it — the transport was torn down when the loop closed, so the
        only observable residue is a possible `httpx` "unclosed client"
        warning on GC, which is acceptable given the alternative is an
        error (the correct place for callers who care is to call
        `provider.aclose()` inside each `asyncio.run` scope before leaving
        it — see the regression tests).
        """
        if self._injected_http_client is not None:
            return self._injected_http_client
        current_loop = asyncio.get_running_loop()
        if self._http_client_loop is not None and current_loop is not self._http_client_loop:
            old_loop = self._http_client_loop
            old_client = self.http_client
            self.http_client = httpx.AsyncClient(timeout=self._timeout)
            # Best-effort close of the prior client. `is_closed` on an
            # `asyncio.AbstractEventLoop` is the canonical "can we schedule
            # work on it" check. If the old loop is alive we submit
            # `aclose()` to it from this thread/loop; if closed, we skip —
            # the transport already went with the loop.
            if not old_loop.is_closed():
                # Loop may transition to closed between the check and the
                # submit — `run_coroutine_threadsafe` raises `RuntimeError`
                # if so, which is benign (we fall through as if the loop was
                # already closed at check time).
                with contextlib.suppress(RuntimeError):
                    asyncio.run_coroutine_threadsafe(old_client.aclose(), old_loop)
        self._http_client_loop = current_loop
        return self.http_client

    async def generate(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> GenerationResult:
        return await self._validated_generate(prompt, output_schema, client=self._get_http_client())

    async def _validated_generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        client: httpx.AsyncClient,
    ) -> GenerationResult:
        """Shared validate-and-retry path for both ``generate()`` and ``infer()``.

        Calls ``_raw_generate`` on the given *client*, then runs the
        ``StructuredOutputValidator`` fence-strip + JSON-parse + retry loop
        against *schema*. Retries also route through the same *client* so
        connection-pool affinity is preserved within a single event loop.
        """
        raw_text = await self._raw_generate(prompt, client=client)

        async def _regenerate(correction_prompt: str) -> str:
            return await self._raw_generate(correction_prompt, client=client)

        return await self._validator.validate_and_retry(
            raw_text,
            schema,
            _regenerate,
            original_prompt=prompt,
        )

    async def _raw_generate(
        self,
        prompt: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> str:
        http = client or self.http_client
        payload = _build_payload(self._model, prompt)
        try:
            response = await http.post(self._generate_url, json=payload)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            _logger.warning("intelligence_unavailable", cause="connect_error", error=str(exc))
            raise IntelligenceUnavailableError from exc
        except httpx.TimeoutException as exc:
            # Per-request deadline violation is a 504 timeout, not a 503
            # availability failure. Reports the httpx-level budget the request
            # was bounded by (``ollama_timeout_seconds``) — distinct from the
            # end-to-end ``extraction_timeout_seconds`` surfaced by
            # ``ExtractionService``. See issue #137.
            _logger.warning(
                "intelligence_timeout",
                budget_seconds=self._timeout_seconds,
                error=str(exc),
            )
            raise IntelligenceTimeoutError(budget_seconds=self._timeout_seconds) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            cause = (
                "http_4xx"
                if _HTTP_CLIENT_ERROR_MIN <= status < _HTTP_SERVER_ERROR_MIN
                else "http_5xx"
            )
            _logger.warning("intelligence_unavailable", cause=cause, status=status)
            raise IntelligenceUnavailableError from exc
        except httpx.RequestError as exc:
            # Catch-all for transport-level failures that the specific handlers
            # above do not cover: ReadError, RemoteProtocolError, WriteError,
            # etc. Without this, those RequestError subclasses escape as raw
            # exceptions and surface as HTTP 500 instead of 503.
            # See issue #49.
            _logger.warning("intelligence_unavailable", cause="request_error", error=str(exc))
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
        # A fresh ``AsyncClient`` is created per ``infer()`` call so that each
        # ``asyncio.run`` scope gets its own loop+client pair. Reusing the
        # instance-level ``self.http_client`` across separate ``asyncio.run``
        # invocations causes ``RuntimeError: Event loop is closed`` on the
        # second call because httpx binds its connection pool to the first
        # loop, which ``asyncio.run`` closes on return (issue #47).
        #
        # Every prompt in the batch shares the same fresh client — and therefore
        # the same event loop — so connection pooling still works within a
        # single batch. Each prompt routes through ``_validated_generate``,
        # the shared helper that both ``generate()`` and this batch path use,
        # running the ``StructuredOutputValidator`` fence-strip + JSON-parse +
        # retry loop against the LangExtract wrapper schema. This ensures the
        # plugin entry path enforces the same CLAUDE.md-mandated "no bypass"
        # invariant as the ``generate()`` path the
        # ``_ValidatingLangExtractAdapter`` in ``extraction_engine.py`` uses.
        async with httpx.AsyncClient(timeout=self._timeout) as batch_client:
            outputs: list[str] = []
            for prompt in batch_prompts:
                result = await self._validated_generate(
                    prompt,
                    LANGEXTRACT_WRAPPER_SCHEMA,
                    client=batch_client,
                )
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
            response = await self._get_http_client().get(self._tags_url)
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError):
            return False
        return True

    async def aclose(self) -> None:
        # Close the current `http_client`. Any earlier client that was
        # replaced by a cross-loop rebind is already unreachable and was
        # torn down when its loop exited.
        await self.http_client.aclose()

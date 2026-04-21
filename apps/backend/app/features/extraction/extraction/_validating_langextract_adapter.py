"""`_ValidatingLangExtractAdapter` — routes every LangExtract model call
through the project's `IntelligenceProvider.generate`, so
`StructuredOutputValidator` (cleanup + retry) runs on every call.

Lives alongside `extraction_engine.py` inside
`app.features.extraction.extraction`. The C5 LangExtract-containment
contract is file-level (see `import-linter-contracts.ini` C5 and the
AST-scan allowlist in `tests/unit/features/extraction/extraction/
test_no_third_party_imports.py`): it enumerates exactly this file, the
engine, and `intelligence/ollama_gemma_provider.py`, and no other file
in the subpackage may `import langextract`. Splitting the adapter out
of `extraction_engine.py` to satisfy Sacred Rule #1 (one class per
file) required adding this file to the allowlist explicitly — being
co-located with the engine does not, on its own, authorise the
import.

**Event-loop bridging.** `ExtractionEngine.extract` runs
`langextract.extract` inside `asyncio.to_thread`, so this adapter's
`infer` executes on a worker thread with no running loop of its own.
`provider.generate` must still run on the application's main event
loop — `get_intelligence_provider()` is `@lru_cache`d, returning a
single `OllamaGemmaProvider` whose `httpx.AsyncClient` connection
pool is bound to that one loop. Calling `generate` from a fresh
loop (e.g., `asyncio.run` inside the worker thread) would trigger
"Event loop is closed" or "Future attached to a different loop"
errors on the second invocation. The adapter therefore captures
the caller's main loop in `__init__` and schedules each generate
coroutine back onto it via `asyncio.run_coroutine_threadsafe`,
blocking on the returned `concurrent.futures.Future` from the
worker thread — LangExtract's own orchestration is synchronous,
so the blocking call is expected.

**Bounded blocking (issue #152).** The blocking `future.result()` call
is always bounded by `timeout_seconds` (sourced from
`Settings.ollama_timeout_seconds`). Without a timeout, an unresponsive
Ollama pins the worker thread indefinitely; sustained hangs exhaust
the thread pool, queue new requests, and degrade the service silently.
On `concurrent.futures.TimeoutError` we cancel the pending future
(best-effort — a coroutine that has already started awaiting a blocking
primitive may still run to completion on the main loop, but any pending
result is discarded when this adapter's caller returns) and raise
`IntelligenceTimeoutError`, which the global exception handler maps to
504. The adapter does NOT own the main loop's executor, so there is
nothing to shut down here. Note: `concurrent.futures.TimeoutError` is
aliased to the builtin `TimeoutError` in CPython 3.11+, so the `except`
clause must distinguish an adapter timeout (future still pending) from
an inner `TimeoutError` raised by the coroutine (future already done)
via `future.done()` — otherwise inner failures would be silently
remapped to `IntelligenceTimeoutError` and lose their cause.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import TYPE_CHECKING, Any

import structlog
from langextract.core.base_model import BaseLanguageModel
from langextract.core.types import ScoredOutput

from app.exceptions import IntelligenceTimeoutError
from app.features.extraction.intelligence.langextract_wrapper_schema import (
    LANGEXTRACT_WRAPPER_SCHEMA,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from app.features.extraction.intelligence.intelligence_provider import IntelligenceProvider


_logger = structlog.get_logger(__name__)


class _ValidatingLangExtractAdapter(BaseLanguageModel):  # pyright: ignore[reportUnusedClass]
    # ^ The adapter is consumed by the adjacent `extraction_engine.py`
    # via cross-module import. Pyright's strict-level `reportUnusedClass`
    # flags definition-only modules, but that is the intended shape here
    # (issue #228 split: one class per file + C5 LangExtract containment).
    """BaseLanguageModel that routes each LangExtract call through the
    caller's `IntelligenceProvider.generate`, so the project's
    `StructuredOutputValidator` (cleanup + retry) runs on every model call.

    See the module docstring for the full rationale (event-loop bridging,
    bounded blocking, and the side-channel used to surface the validator's
    retry count back to `ExtractionEngine`).
    """

    def __init__(
        self,
        inner: IntelligenceProvider,
        main_loop: asyncio.AbstractEventLoop,
        *,
        timeout_seconds: float,
    ) -> None:
        super().__init__()
        self._inner = inner
        self._main_loop = main_loop
        self._timeout_seconds = timeout_seconds
        # Side-channel for propagating the validator's retry count out to
        # `_to_raw_extractions` (issue #135). LangExtract's `ScoredOutput`
        # is a frozen dataclass with only `score` and `output` fields, so
        # `GenerationResult.attempts` cannot ride along in-band. We instead
        # record the MAX attempts observed across every prompt this adapter
        # drove. `ExtractionEngine.extract` invokes LangExtract with
        # `batch_length=1, max_workers=1` and a single concatenated text,
        # so in normal operation this is effectively "attempts for this
        # extraction call". Max (rather than last or sum) is conservative:
        # if any prompt in the batch retried N times, every field in the
        # resulting `RawExtraction` list reflects N attempts. 0 means no
        # prompt was driven (short-circuit paths), in which case the
        # engine keeps the legacy 1 default.
        self._max_observed_attempts: int = 0

    @property
    def max_observed_attempts(self) -> int:
        """MAX `GenerationResult.attempts` seen across this adapter's prompts.

        Zero until `infer` processes at least one prompt. After that, the
        engine reads this attribute to stamp every declared field in the
        resulting `RawExtraction` list with the validator's retry count.
        """
        return self._max_observed_attempts

    def infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,  # noqa: ARG002 - LangExtract passes orchestrator kwargs that we do not consume
    ) -> Iterator[Sequence[ScoredOutput]]:
        for prompt in batch_prompts:
            future = asyncio.run_coroutine_threadsafe(
                self._inner.generate(prompt, LANGEXTRACT_WRAPPER_SCHEMA),
                self._main_loop,
            )
            try:
                result = future.result(timeout=self._timeout_seconds)
            except concurrent.futures.TimeoutError as exc:
                # In CPython 3.11+, `concurrent.futures.TimeoutError is
                # TimeoutError is asyncio.TimeoutError`. A bare `except`
                # on the class would also catch an inner `TimeoutError`
                # raised by the coroutine body, conflating a hung Ollama
                # (this adapter's concern) with an inner-provider timeout
                # (not our concern). There is also a boundary race:
                # `future.result(timeout=...)` may time out and THEN the
                # future may settle before we inspect it. We distinguish
                # by `future.done()`: a done future means the coroutine
                # settled (success or exception) — call `future.result()`
                # with no timeout so a just-completed success continues
                # normally and a coroutine-raised `TimeoutError` still
                # propagates unchanged. Only a still-pending future
                # should be cancelled and remapped to our domain error.
                if future.done():
                    result = future.result()
                else:
                    # Best-effort cancel — a coroutine already blocked in a
                    # syscall on the main loop may still complete, but we stop
                    # caring about its result. Without this bound, a hung
                    # Ollama would pin this worker thread forever (issue #152).
                    future.cancel()
                    # Emit a breadcrumb BEFORE raising so operators can see
                    # that LangExtract's inference loop — not a downstream
                    # 504 in middleware — was the actual hang. Every other
                    # timeout/failure path in the pipeline (`OllamaGemmaProvider`,
                    # `SpanResolver`, `StructuredOutputValidator`) already emits
                    # a structlog event; this one was silent (issue #334).
                    # Naming mirrors `ollama_gemma_provider.py`'s
                    # `intelligence_timeout` event for discoverability.
                    _logger.warning(
                        "langextract_adapter_timeout",
                        budget_seconds=self._timeout_seconds,
                        prompts_in_batch=len(batch_prompts),
                        original_exc_type=type(exc).__name__,
                    )
                    raise IntelligenceTimeoutError(
                        budget_seconds=self._timeout_seconds,
                    ) from None
            # Capture the validator retry count BEFORE yielding (issue #135).
            # Max across the batch so a single retried prompt is visible
            # even when other prompts succeeded on the first try.
            self._max_observed_attempts = max(
                self._max_observed_attempts,
                result.attempts,
            )
            yield [ScoredOutput(score=1.0, output=json.dumps(result.data))]

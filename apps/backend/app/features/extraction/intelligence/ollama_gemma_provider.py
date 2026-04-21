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
import json
import weakref
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

# Hard upper bound on how long we will block waiting for an old loop to
# run the stale client's ``aclose()`` coroutine after we scheduled it via
# ``run_coroutine_threadsafe``. If the old loop is not running the
# coroutine, the caller-driven ``await`` on ``asyncio.wrap_future`` would
# block indefinitely; this deadline bounds the teardown so we always fall
# back to the best-effort close on the current loop (issue #234 review
# feedback). Expressed in seconds; 2.0 s is comfortably above any real
# ``aclose()`` but fast enough that a stuck teardown does not visibly
# stall callers.
_STALE_CLIENT_ACLOSE_TIMEOUT_SECONDS = 2.0

# Ollama ``/api/generate`` ``options`` keys the provider is willing to forward
# from caller-supplied kwargs on the ``infer()`` path (issue #385). LangExtract
# passes whatever ``language_model_params`` dict the user hands to
# ``lx.extract`` straight through to ``BaseLanguageModel.infer`` as ``**kwargs``;
# before this allowlist the provider silently dropped every entry. Keeping the
# list explicit (rather than forwarding ``**kwargs`` wholesale) means:
#
#   * only kwargs whose names appear in this allowlist can affect Ollama
#     sampling; all other caller-supplied kwargs are ignored and logged at
#     DEBUG rather than forwarded,
#   * unknown / future Ollama options do not leak into the payload without a
#     deliberate code change (and a matching test),
#   * the ``generate()`` path is unaffected — it never receives caller kwargs,
#     so the module-level ``_DEFAULT_SAMPLING_OPTIONS`` baseline (below)
#     remains in force for validator retries.
#
# Sampling keys only (seed, temperature, top_p, top_k, num_ctx, num_predict,
# repeat_penalty, mirostat family). Streaming / format / keep_alive / raw are
# deliberately excluded: they are transport-shape concerns the provider owns,
# not sampling concerns callers should override.
_OLLAMA_SAMPLING_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "seed",
        "num_ctx",
        "num_predict",
        "repeat_penalty",
        "mirostat",
        "mirostat_tau",
        "mirostat_eta",
    },
)

# Default sampling options applied when the caller does not override them.
# Pinning ``temperature=0`` keeps the validator-retry determinism contract from
# issue #136 intact for callers (including the ``generate()`` path and plain
# ``infer()`` with no kwargs). This is a module-level constant, not a
# ``Settings``-backed field — operators who need to change the baseline do so
# here, not via env var. Caller overrides win — see ``_merge_sampling_options``.
_DEFAULT_SAMPLING_OPTIONS: dict[str, Any] = {"temperature": 0}


def _build_generate_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/generate"


def build_tags_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/tags"


def _merge_sampling_options(**kwargs: Any) -> tuple[dict[str, Any], list[str]]:
    """Return the merged Ollama ``options`` dict plus the ignored-kwarg key list.

    Splits the raw LangExtract-forwarded kwargs dict into two buckets:

    * keys in ``_OLLAMA_SAMPLING_OPTION_KEYS`` whose value is not ``None``
      are copied over the ``_DEFAULT_SAMPLING_OPTIONS`` baseline (caller
      wins) and returned as the first element of the tuple. An allowlisted
      key whose value IS ``None`` is treated as "not provided" — the
      default is preserved rather than being clobbered with ``None``.
      Forwarding ``null`` to Ollama would break the determinism contract
      for ``temperature`` and risks a server-side 400 for keys Ollama
      validates (e.g. ``seed``),
    * keys NOT in the allowlist are collected into a list and returned as
      the second element so the caller can log them for observability
      (issue #385: operators need to see drift when LangExtract forwards
      something new).

    The default baseline is copied rather than mutated so concurrent
    ``infer()`` calls do not race on a shared dict.
    """
    options: dict[str, Any] = dict(_DEFAULT_SAMPLING_OPTIONS)
    ignored: list[str] = []
    for key, value in kwargs.items():
        if key in _OLLAMA_SAMPLING_OPTION_KEYS:
            if value is not None:
                options[key] = value
        else:
            ignored.append(key)
    return options, ignored


def _build_payload(
    model: str,
    prompt: str,
    *,
    sampling_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # ``format="json"`` engages Ollama's native JSON-constrained decoding so the
    # server returns well-formed JSON on the first attempt instead of forcing
    # ``StructuredOutputValidator`` to re-prompt for parse errors on every
    # request. ``options`` pins sampling parameters; without overrides it keeps
    # the module-level ``_DEFAULT_SAMPLING_OPTIONS`` baseline (``temperature=0``
    # from issue #136) so validator retries repeat from a stable baseline
    # rather than drifting between attempts. When the caller supplies
    # ``sampling_options`` (the ``infer()`` path on a LangExtract-forwarded
    # kwarg — issue #385), those values replace the baseline per-key. The
    # validator still enforces our downstream schema shape, but it no longer
    # pays the malformed-output retry cost on the happy path.
    options = sampling_options if sampling_options is not None else dict(_DEFAULT_SAMPLING_OPTIONS)
    return {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": options,
    }


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
        # Per-loop `asyncio.Lock`s that serialize the rebuild critical
        # section so concurrent entrants on the same loop do not both
        # allocate a fresh client (issue #234). A single instance-level
        # lock will not work because `asyncio.Lock` binds to the first
        # loop it is acquired on, and the rebuild path is reached on
        # loops that alternate. The dict is weakly keyed — see
        # `_rebuild_lock_for` — so dead loops do not retain locks.
        self._rebuild_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )

    def _rebuild_lock_for(self, loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
        """Return the ``asyncio.Lock`` guarding rebuild on *loop*.

        Lazily creates one lock per event loop. The dict is a
        ``WeakKeyDictionary`` so that once an event loop is garbage
        collected its lock entry disappears — long-lived providers
        spanning many short-lived loops (e.g. test suites) do not
        accumulate lock references and leak them. Lookup/insert here is
        synchronous and executes between ``await`` points under the
        cooperative scheduler, so it is safe from same-loop interleave.
        """
        lock = self._rebuild_locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._rebuild_locks[loop] = lock
        return lock

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Return an `AsyncClient` bound to the currently-running event loop.

        If the caller injected a client via the constructor, return it
        unchanged — they own its loop affinity. Otherwise rebuild
        ``self.http_client`` when the running loop differs from the one
        the current client was first used on. This fixes the cross-loop
        regression in issue #132 where a single provider instance used
        across `asyncio.run` scopes would raise ``RuntimeError: Event loop
        is closed`` on the second call.

        Concurrency (issue #234): the check-and-swap is guarded by a
        per-loop ``asyncio.Lock`` so that two concurrent entrants on the
        same loop serialize — the first performs the rebuild, the
        second re-checks inside the lock and shares the already-rebuilt
        instance. Without the lock, the ``await`` on the old client's
        ``aclose()`` below would be a scheduling point where a sibling
        coroutine could slip in and double-allocate; only the second
        write would be retained and the first fresh client would leak.

        Old-client cleanup (issue #234): on rebuild, the outgoing
        client is ``aclose()``d before the new client is returned. If
        the old loop is still alive we schedule ``aclose()`` on it via
        ``run_coroutine_threadsafe`` and await the wrapped future so the
        transport tears down on the loop it was bound to. If the old
        loop has already been closed, we fall back to awaiting
        ``aclose()`` on the current loop; real httpx clients bound to a
        dead loop may raise ``RuntimeError`` inside transport teardown
        because their connection pool is unreachable — we log and drop
        that error because the sockets were already reaped alongside
        the dead loop (and surfacing it here would mask a successfully
        rebuilt provider from callers).
        """
        if self._injected_http_client is not None:
            return self._injected_http_client
        current_loop = asyncio.get_running_loop()
        # Fast path: already bound to this loop. Avoids the lock
        # acquisition cost on every request. The sync read is atomic
        # under cooperative scheduling.
        if self._http_client_loop is current_loop:
            return self.http_client
        async with self._rebuild_lock_for(current_loop):
            # Re-check inside the lock: another entrant may have
            # completed the rebuild while we awaited the lock.
            if self._http_client_loop is current_loop:
                return self.http_client
            old_loop = self._http_client_loop
            if old_loop is None:
                # First-ever binding. The eagerly-constructed client in
                # ``__init__`` has not yet touched a loop, so we simply
                # stamp it onto the current loop — no rebuild, no stale
                # client to close.
                self._http_client_loop = current_loop
                return self.http_client
            old_client = self.http_client
            self.http_client = httpx.AsyncClient(timeout=self._timeout)
            self._http_client_loop = current_loop
            await self._aclose_stale_client(old_client, old_loop)
        return self.http_client

    async def _aclose_stale_client(
        self,
        old_client: httpx.AsyncClient,
        old_loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Await close of a client bound to a previous event loop (issue #234).

        Three disjoint cases:

        1. Old loop is alive AND running — schedule ``aclose()`` on it
           via ``run_coroutine_threadsafe`` and await the wrapped future
           (bounded by ``_STALE_CLIENT_ACLOSE_TIMEOUT_SECONDS``) so
           sockets tear down on the loop that opened them.
        2. Old loop is not closed but is not running (e.g. ``loop.stop()``
           without ``close()``). Scheduling via
           ``run_coroutine_threadsafe`` would block forever because
           nothing advances the loop, so we skip it and fall through to
           the current-loop best-effort branch.
        3. Old loop is closed — schedule is impossible. Fall back to
           ``await old_client.aclose()`` on the current loop. Real
           ``httpx.AsyncClient`` instances whose connection pool was
           bound to a dead loop may raise ``RuntimeError`` inside
           transport teardown; we log and drop the error because the
           transport sockets already went with the dead loop.

        The timeout guards case (1) against a loop that was running at
        submit time but stopped (or hung) before draining the scheduled
        coroutine — without it the ``asyncio.wrap_future`` await would
        block forever on the current loop (issue #234 review feedback).
        """
        if not old_loop.is_closed() and old_loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(old_client.aclose(), old_loop)
            except RuntimeError:
                # Loop transitioned closed between the state checks and
                # ``run_coroutine_threadsafe`` submit. Fall through to
                # the current-loop best-effort branch below. ``exc_info``
                # surfaces the scheduler's own message (issue #335) so
                # operators see the full teardown trail, not just the
                # event name.
                _logger.warning("stale_client_old_loop_raced_closed", exc_info=True)
            else:
                try:
                    await asyncio.wait_for(
                        asyncio.wrap_future(future),
                        timeout=_STALE_CLIENT_ACLOSE_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    # The old loop stopped (or blocked) after submission
                    # and never drained the scheduled ``aclose()``.
                    # Fall back to the current-loop best-effort branch
                    # rather than hanging. The scheduled coroutine
                    # remains on the old loop's queue; if that loop is
                    # subsequently closed its sockets go with it, and if
                    # it is later restarted the close runs harmlessly
                    # against the already-detached client. Logged at
                    # ``warning`` (issue #335) because a bounded
                    # teardown that hit its deadline is an operator
                    # signal, not a debug note.
                    _logger.warning("stale_client_old_loop_close_timed_out")
                except Exception:  # noqa: BLE001 — best-effort teardown must not crash the rebuild; all failure modes are logged
                    # ``aclose()`` itself raised inside the old loop
                    # (bad socket, httpx internal error, etc.). The
                    # scheduled coroutine propagates its exception
                    # back through ``asyncio.wrap_future``. Before
                    # issue #335 this fell through uncaught and
                    # crashed the rebuild; the fix captures it as
                    # best-effort with ``exc_info`` so the full
                    # traceback reaches operators.
                    _logger.warning("stale_client_aclose_failed", exc_info=True)
                else:
                    return
        try:
            await old_client.aclose()
        except Exception:  # noqa: BLE001 — best-effort teardown must not crash the rebuild; all failure modes are logged
            # Expected when the old client's transport was bound to a
            # now-dead loop: httpcore raises ``RuntimeError: Event
            # loop is closed``. Other failure modes (bad socket, httpx
            # internal error) are also possible here — before issue
            # #335 only ``RuntimeError`` was caught and the log was at
            # ``debug`` with just ``error=str(exc)``, so operators
            # running at the default ``WARNING`` level saw nothing. The
            # fix widens the catch to ``Exception``, raises the level
            # to ``warning``, and attaches ``exc_info=True`` so the
            # full traceback renders into the ``exception`` field via
            # structlog's ``format_exc_info`` processor. The sockets
            # were reaped when the loop closed, so the leak window is
            # already zero and we still do not re-raise — the fresh
            # client on the current loop is valid.
            _logger.warning("stale_client_aclose_failed", exc_info=True)

    async def generate(
        self,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> GenerationResult:
        client = await self._get_http_client()
        return await self._validated_generate(prompt, output_schema, client=client)

    async def _validated_generate(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        client: httpx.AsyncClient,
        sampling_options: dict[str, Any] | None = None,
    ) -> GenerationResult:
        """Shared validate-and-retry path for both ``generate()`` and ``infer()``.

        Calls ``_raw_generate`` on the given *client*, then runs the
        ``StructuredOutputValidator`` fence-strip + JSON-parse + retry loop
        against *schema*. Retries also route through the same *client* so
        connection-pool affinity is preserved within a single event loop.

        ``sampling_options``, when provided, threads caller-supplied Ollama
        ``options`` (temperature, top_p, seed, …) into the payload so
        LangExtract-forwarded kwargs on the ``infer()`` path are honored
        (issue #385). Retries reuse the same options dict so every
        regeneration samples under the caller's requested parameters rather
        than drifting back to defaults on retry.
        """
        raw_text = await self._raw_generate(
            prompt,
            client=client,
            sampling_options=sampling_options,
        )

        async def _regenerate(correction_prompt: str) -> str:
            return await self._raw_generate(
                correction_prompt,
                client=client,
                sampling_options=sampling_options,
            )

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
        client: httpx.AsyncClient,
        sampling_options: dict[str, Any] | None = None,
    ) -> str:
        # ``client`` is required (not defaulted to ``self.http_client``) so any
        # caller is forced to go through ``_get_http_client()`` for loop-bound
        # rebinding. Falling back to ``self.http_client`` bypassed the
        # rebuild-on-loop-switch logic and was a foot-gun for any new caller
        # that forgot to call ``_get_http_client()`` first — Pyright strict
        # now surfaces the mistake at author time (issue #277).
        # ``sampling_options`` is threaded through from ``infer()`` so
        # LangExtract-forwarded sampling kwargs land in the Ollama payload
        # (issue #385). When ``None`` (the ``generate()`` path), ``_build_payload``
        # falls back to the module-level default sampling options, including
        # ``temperature=0``.
        payload = _build_payload(self._model, prompt, sampling_options=sampling_options)
        try:
            response = await client.post(self._generate_url, json=payload)
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
            # Ollama (and proxies that front it) may answer a 200 with a JSON
            # body that omits ``response`` and instead carries an explicit
            # diagnostic under the ``error`` key — e.g.
            # ``{"error": "model not loaded"}`` when the runner has not yet
            # warmed the requested weights. Before surfacing the classified
            # ``missing_response_field`` cause, lift that string onto the log
            # line under ``ollama_error`` so operators debugging failures see
            # the upstream's own explanation instead of a bare classification.
            # The log field is elided (not emitted as ``None``) when the body
            # either has no ``error`` key or carries a non-string value there;
            # forwarding a non-string would defeat the diagnostic purpose and
            # pollute the log schema. The ``error`` value is a server-owned
            # string and never echoes prompt content, so there is no
            # prompt-leak concern (see issue #333). See also the guidance in
            # the CLAUDE.md "Forbidden Patterns" against silently swallowing
            # error bodies.
            log_kwargs: dict[str, Any] = {"cause": "missing_response_field"}
            ollama_error = body.get("error")
            if isinstance(ollama_error, str):
                log_kwargs["ollama_error"] = ollama_error
            _logger.warning("intelligence_unavailable", **log_kwargs)
            raise IntelligenceUnavailableError from None
        return response_text

    async def _validated_generate_batch(
        self,
        batch_prompts: Sequence[str],
        *,
        sampling_options: dict[str, Any] | None = None,
    ) -> list[str]:
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
        #
        # ``sampling_options`` is forwarded from ``infer()`` to decorate every
        # prompt in the batch with the caller-supplied Ollama options (issue
        # #385). The same dict is shared across prompts — sampling is a
        # per-call concern, not per-prompt, so reusing it is correct; no
        # prompt is mutating the dict.
        async with httpx.AsyncClient(timeout=self._timeout) as batch_client:
            outputs: list[str] = []
            for prompt in batch_prompts:
                result = await self._validated_generate(
                    prompt,
                    LANGEXTRACT_WRAPPER_SCHEMA,
                    client=batch_client,
                    sampling_options=sampling_options,
                )
                outputs.append(json.dumps(result.data))
            return outputs

    def infer(
        self,
        batch_prompts: Sequence[str],
        **kwargs: Any,
    ) -> Iterator[Sequence[ScoredOutput]]:
        """Run LangExtract-driven inference and yield one ``ScoredOutput`` per prompt.

        LangExtract's orchestrator forwards ``language_model_params`` (from
        ``lx.extract(..., language_model_params={"temperature": 0.7})``) to
        this method as ``**kwargs``. The provider threads the allowlisted
        Ollama sampling options (temperature, top_p, top_k, seed, num_ctx,
        num_predict, repeat_penalty, mirostat family) into the
        ``/api/generate`` payload so caller overrides actually reach Ollama
        (issue #385). Any non-sampling kwarg LangExtract forwards —
        ``format_type``, ``constraint``, ``model_url``, …, or a new option
        added in a future LangExtract release — is logged at DEBUG under the
        ``ollama_provider_ignored_kwargs`` event so operators can see drift
        and add it to the allowlist deliberately if it turns out to matter.
        """
        sampling_options, ignored_keys = _merge_sampling_options(**kwargs)
        if ignored_keys:
            _logger.debug(
                "ollama_provider_ignored_kwargs",
                keys=sorted(ignored_keys),
            )
        validated_outputs = asyncio.run(
            self._validated_generate_batch(
                batch_prompts,
                sampling_options=sampling_options,
            ),
        )
        for output in validated_outputs:
            yield [ScoredOutput(score=1.0, output=output)]

    async def health_check(self) -> bool:
        try:
            client = await self._get_http_client()
            response = await client.get(self._tags_url)
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError):
            return False
        return True

    async def aclose(self) -> None:
        # Close the current `http_client`. Any earlier client that was
        # replaced by a cross-loop rebind is already unreachable and was
        # torn down when its loop exited.
        await self.http_client.aclose()

"""Integration tests for OllamaGemmaProvider.

Uses `respx` to intercept outbound `httpx` calls against the configured Ollama
base URL. The provider is constructed directly so the `generate()` and retry
paths are exercised without going through FastAPI's DI layer — the DI layer's
job (`create_app(settings=...)` → `app.state.settings` → `Depends(get_…)`) is
covered by `tests/integration/test_settings_dependency_propagation.py`.

Also verifies the LangExtract plugin discovery path: importing the provider
module triggers the `@register(r"^gemma", ...)` decorator, and
`langextract.providers.router.resolve("gemma4:e2b")` returns our class.

Per issue #329 this module also hosts the cross-loop regression suite (issue
#132) that uses a bare-metal ``socketserver.ThreadingTCPServer``. Those tests
bind real sockets on 127.0.0.1, which belongs at integration level rather than
inside the unit suite's "no network" boundary.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

import httpx
import pytest
import respx
from langextract import factory as lx_factory
from langextract.providers.router import resolve

from app.core.config import Settings
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.ollama_gemma_provider import (
    OllamaGemmaProvider,
)
from app.features.extraction.intelligence.structured_output_validator import (
    StructuredOutputValidator,
)

_NAME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}},
}


def _build_provider(settings: Settings | None = None) -> OllamaGemmaProvider:
    real_settings = settings or Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env
    validator = StructuredOutputValidator(
        settings=real_settings,
        correction_prompt_builder=CorrectionPromptBuilder(),
    )
    return OllamaGemmaProvider(settings=real_settings, validator=validator)


@respx.mock
async def test_generate_sends_configured_model_tag_through_respx() -> None:
    settings = Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env
    route = respx.post(f"{settings.ollama_base_url}/api/generate").mock(
        return_value=httpx.Response(
            200,
            json={"response": '{"name":"Alice"}'},
        ),
    )

    provider = _build_provider(settings)
    try:
        result = await provider.generate("hi", _NAME_SCHEMA)
    finally:
        await provider.aclose()

    assert result.data == {"name": "Alice"}
    assert result.attempts == 1
    assert route.called
    sent_body = route.calls.last.request.content
    assert b'"model"' in sent_body
    assert settings.ollama_model.encode() in sent_body


@respx.mock
async def test_retry_loop_calls_post_twice_on_malformed_first_response() -> None:
    settings = Settings()  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env
    route = respx.post(f"{settings.ollama_base_url}/api/generate").mock(
        side_effect=[
            httpx.Response(200, json={"response": "not valid json"}),
            httpx.Response(200, json={"response": '{"name":"Alice"}'}),
        ],
    )

    provider = _build_provider(settings)
    try:
        result = await provider.generate("hi", _NAME_SCHEMA)
    finally:
        await provider.aclose()

    assert result.attempts == 2
    assert result.data == {"name": "Alice"}
    assert route.call_count == 2


async def test_lifespan_shutdown_closes_provider_on_app_state() -> None:
    """`_lifespan` closes whatever `OllamaGemmaProvider` is on `app.state`.

    This test builds a provider directly and installs it on `app.state`,
    then drives the lifespan context manually (httpx's `ASGITransport` does
    not fire lifespan events). Mirrors what uvicorn does on startup/shutdown.
    """
    from app.main import create_app

    app = create_app()
    settings: Settings = app.state.settings
    provider = _build_provider(settings)
    app.state.intelligence_provider = provider

    async with app.router.lifespan_context(app):
        assert provider.http_client.is_closed is False

    assert provider.http_client.is_closed is True


def test_langextract_plugin_discovery_resolves_to_ollama_gemma_provider() -> None:
    # Verifies that LangExtract's pattern-resolver picks *our* provider for the
    # `gemma*` model family. We share the `^gemma` pattern with the built-in
    # OllamaLanguageModel (priority=10); our priority=20 wins the tie.
    resolve.cache_clear()  # type: ignore[attr-defined]  # resolve is @lru_cache-wrapped

    resolved = resolve("gemma4:e2b")

    assert resolved is OllamaGemmaProvider


def test_custom_provider_priority_beats_builtin_ollama_provider() -> None:
    # Defense-in-depth for the priority decision: verify the router actually
    # orders our class ahead of the built-in `OllamaLanguageModel` pattern
    # matcher. If this ever regresses (e.g. someone lowers our priority), this
    # test catches it explicitly instead of relying on the happy-path resolve.
    from langextract.providers.ollama import OllamaLanguageModel

    resolve.cache_clear()  # type: ignore[attr-defined]  # resolve is @lru_cache-wrapped
    resolved = resolve("gemma4:e2b")
    assert resolved is OllamaGemmaProvider
    assert resolved is not OllamaLanguageModel


async def test_langextract_factory_create_model_instantiates_our_provider() -> None:
    # This is the exact path LangExtract's orchestration takes: build a
    # ModelConfig from a model_id and ask the factory to create the model.
    # The factory calls provider_class(**kwargs) with kwargs["model_id"] set
    # and any env-derived extras merged in. This test exists because the
    # first implementation accepted only (settings, validator) as keyword
    # arguments and LangExtract's `provider_class(model_id=...)` invocation
    # raised `unexpected keyword argument 'model_id'` — a silent blocker
    # that `resolve(...)` alone would not catch.
    resolve.cache_clear()  # type: ignore[attr-defined]  # resolve is @lru_cache-wrapped
    config = lx_factory.ModelConfig(model_id="gemma4:e2b")

    model = lx_factory.create_model(config)

    try:
        assert isinstance(model, OllamaGemmaProvider)
        # The model_id passed to the factory must be the tag the provider
        # will send to Ollama on every POST (overriding Settings default).
        assert model._model == "gemma4:e2b"  # noqa: SLF001 — exercising constructor contract
    finally:
        await model.aclose()


# ── Cross-loop regression tests (issue #132, moved here per issue #329) ──
#
# Issue #132 is the follow-up to #47. PR #88 moved the `infer()` batch path to
# a fresh `AsyncClient` per `asyncio.run` scope, but the instance-level
# `self.http_client` that backs `generate()` is still constructed in `__init__`
# and, once used, binds its connection pool to the first event loop it sees.
# A second call on a fresh loop then fails with `RuntimeError: Event loop is
# closed`. The fix rebinds the instance client lazily when the running loop
# differs from the one it was last bound to.
#
# These tests use a bare-metal `socketserver.ThreadingTCPServer` rather than
# `respx` because `respx` intercepts at the transport layer before httpx
# reaches its connection pool — the pool is exactly what carries the
# loop-binding affinity, so mocking it out hides the very bug we need to pin.
# Previously hosted in the unit suite; moved to integration per issue #329
# because the in-process TCP bind is real network.


class _StaticOllamaHandler(http.server.BaseHTTPRequestHandler):
    """Minimal Ollama stub that answers `POST /api/generate` with a fixed body.

    The response body wraps a JSON payload that satisfies the LangExtract
    wrapper schema (`{"extractions": []}`). For `generate()` scenarios that
    need a different schema, callers override `_RESPONSE_BODY` before
    constructing the server.
    """

    protocol_version = "HTTP/1.1"
    _RESPONSE_BODY: bytes = json.dumps({"response": '{"extractions":[]}'}).encode()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self._RESPONSE_BODY)))
        # `Connection: close` keeps the fixture teardown deterministic. Under
        # `keep-alive`, httpx's per-loop connection pool lingers inside a
        # closed event loop and the ThreadingTCPServer handler thread blocks
        # in `rfile.read` waiting for the next request that never arrives,
        # so `server.shutdown()` then hangs for minutes.
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(self._RESPONSE_BODY)

    def log_message(self, *_args: object, **_kwargs: object) -> None:
        # Silence the default stderr access log; tests treat the server as
        # a transparent fixture.
        return


@pytest.fixture
def local_ollama_stub() -> Iterator[str]:
    """Start a local HTTP stub that answers `/api/generate` and yield its base URL.

    The stub is a full in-process TCP server, not a transport mock, so httpx
    opens real connections whose pool is bound to the running event loop — the
    exact code path under test in the cross-loop regression.
    """

    # `allow_reuse_address` must be a *class* attribute to take effect (it's
    # consulted inside `server_bind` before instantiation). Setting it on the
    # instance after bind is a no-op, so subclass here instead.
    class _ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    server = _ReusableThreadingTCPServer(("127.0.0.1", 0), _StaticOllamaHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _build_stub_provider(base_url: str, *, max_retries: int = 3) -> OllamaGemmaProvider:
    """Build a provider pointed at the local TCP stub with a short timeout.

    Delegates validator/provider wiring to ``_build_provider`` so there is a
    single source of truth for the provider construction — only the
    stub-specific ``Settings`` fields are owned here.
    """
    settings = Settings(  # type: ignore[reportCallIssue]  # pydantic-settings loads fields from env
        ollama_base_url=base_url,
        ollama_model="gemma4:e2b",
        ollama_timeout_seconds=5.0,
        structured_output_max_retries=max_retries,
    )
    return _build_provider(settings)


_EXTRACTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["extractions"],
    "properties": {"extractions": {"type": "array"}},
}


def test_generate_survives_rebinding_to_fresh_event_loop_across_calls(
    local_ollama_stub: str,
) -> None:
    """Regression: issue #132 — a provider instance must survive N `generate()`
    calls even when each lands on a freshly created event loop.

    Before the fix, the first `generate()` on loop L1 binds `self.http_client`'s
    connection pool to L1. L1 is then closed. The second `generate()` on a
    brand-new loop L2 finds the cached client pinned to the now-dead L1 and
    raises `RuntimeError: Event loop is closed`. The fix rebinds the instance
    client lazily when the running loop differs from the one it is bound to.

    Uses a real in-process HTTP stub (not respx) because loop binding only
    manifests when httpx's connection pool is actually exercised — respx
    intercepts above that layer.
    """
    # Provider is built eagerly; `http_client` is the auto-constructed real
    # `httpx.AsyncClient`. No DI override — exactly the production shape.
    provider = _build_stub_provider(local_ollama_stub)

    loop1 = asyncio.new_event_loop()
    try:
        first = loop1.run_until_complete(provider.generate("p1", _EXTRACTIONS_SCHEMA))
    finally:
        loop1.close()
    assert first.data == {"extractions": []}

    loop2 = asyncio.new_event_loop()
    try:
        second = loop2.run_until_complete(provider.generate("p2", _EXTRACTIONS_SCHEMA))
        assert second.data == {"extractions": []}
    finally:
        # Close the provider on the same loop that just used it so the
        # rebound `http_client` tears down cleanly instead of leaking a
        # socket pool through `loop2.close()`.
        loop2.run_until_complete(provider.aclose())
        loop2.close()


def test_infer_survives_repeated_calls_on_same_provider(
    local_ollama_stub: str,
) -> None:
    """Regression guard for issue #132's stated reproduction.

    The batch-level fresh-client fix landed in PR #88 made this pass; this
    test pins the contract so the second `infer()` call on the same provider
    never regresses back to `RuntimeError: Event loop is closed`. Uses a
    real HTTP stub so the bug would actually manifest if the fresh-per-batch
    invariant in `_validated_generate_batch` ever regressed.
    """
    provider = _build_stub_provider(local_ollama_stub, max_retries=1)

    try:
        first_results = list(provider.infer(["p1"]))
        second_results = list(provider.infer(["p2"]))

        assert len(first_results) == 1
        assert len(second_results) == 1
        assert json.loads(first_results[0][0].output) == {"extractions": []}
        assert json.loads(second_results[0][0].output) == {"extractions": []}
    finally:
        # Close the eagerly-created instance `http_client` so the test
        # does not leak a real `httpx.AsyncClient` (the `infer()` path
        # uses its own per-batch client, but `__init__` still constructs
        # the instance client for `generate()`/`health_check()` paths).
        asyncio.run(provider.aclose())


def test_generate_single_call_still_succeeds_positive_regression(
    local_ollama_stub: str,
) -> None:
    """Positive regression: the single-call happy path must keep working.

    The loop-aware rebind path added for issue #132 must not break the
    steady-state case where the provider sees one event loop for its entire
    lifetime.
    """
    provider = _build_stub_provider(local_ollama_stub)

    async def _run_generate() -> Any:
        try:
            return await provider.generate("p1", _EXTRACTIONS_SCHEMA)
        finally:
            # Close the httpx client inside the same `asyncio.run` scope so
            # it tears down cleanly rather than leaking once the loop exits.
            await provider.aclose()

    result = asyncio.run(_run_generate())

    assert result.data == {"extractions": []}

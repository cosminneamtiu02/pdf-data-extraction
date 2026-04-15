"""Integration tests for OllamaGemmaProvider.

Uses `respx` to intercept outbound `httpx` calls against the configured Ollama
base URL. The provider is constructed directly so the `generate()` and retry
paths are exercised without going through FastAPI's DI layer — the DI layer's
job (`create_app(settings=...)` → `app.state.settings` → `Depends(get_…)`) is
covered by `tests/integration/test_settings_dependency_propagation.py`.

Also verifies the LangExtract plugin discovery path: importing the provider
module triggers the `@register(r"^gemma", ...)` decorator, and
`langextract.providers.router.resolve("gemma4:e2b")` returns our class.
"""

from __future__ import annotations

from typing import Any

import httpx
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

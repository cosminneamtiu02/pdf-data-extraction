"""Unit tests for OllamaGemmaProvider — the dual-interface Ollama plugin.

These tests exercise the internal `IntelligenceProvider` side of the class:
`async generate`, `async health_check`, and `async aclose`. They never touch a
real Ollama and never start LangExtract's orchestration — the LangExtract-facing
`infer` path is covered by the integration-test file.

The fake async client is hand-written in the same style as
`test_structured_output_validator.py`: no unittest.mock, no pytest-mock. Each
scripted response is a `_FakeResponse` instance or an exception to be raised
when `post` or `get` is awaited.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

import httpx
import pytest

from app.core.config import Settings
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.intelligence_provider import (
    IntelligenceProvider,
)
from app.features.extraction.intelligence.intelligence_unavailable_error import (
    IntelligenceUnavailableError,
)
from app.features.extraction.intelligence.ollama_gemma_provider import (
    OllamaGemmaProvider,
)
from app.features.extraction.intelligence.structured_output_validator import (
    StructuredOutputValidator,
)

_NAME_STRING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}},
}


class _FakeResponse:
    def __init__(
        self,
        *,
        body: dict[str, Any] | None = None,
        status_code: int = 200,
        status_error: httpx.HTTPStatusError | None = None,
    ) -> None:
        self._body = body or {}
        self.status_code = status_code
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeAsyncClient:
    def __init__(
        self,
        *,
        post_outcomes: Sequence[_FakeResponse | BaseException] = (),
        get_outcomes: Sequence[_FakeResponse | BaseException] = (),
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._post_outcomes: list[_FakeResponse | BaseException] = list(post_outcomes)
        self._get_outcomes: list[_FakeResponse | BaseException] = list(get_outcomes)
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[str] = []
        self.aclose_calls: int = 0
        self.timeout = timeout or httpx.Timeout(30.0)
        self.is_closed = False

    async def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse:
        self.post_calls.append((url, json))
        if not self._post_outcomes:
            pytest.fail("FakeAsyncClient.post invoked more times than scripted")
        outcome = self._post_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def get(self, url: str) -> _FakeResponse:
        self.get_calls.append(url)
        if not self._get_outcomes:
            pytest.fail("FakeAsyncClient.get invoked more times than scripted")
        outcome = self._get_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        self.aclose_calls += 1
        self.is_closed = True


def _build_settings(
    *,
    base_url: str = "http://host.docker.internal:11434",
    model: str = "gemma4:e2b",
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
) -> Settings:
    return Settings(
        ollama_base_url=base_url,
        ollama_model=model,
        ollama_timeout_seconds=timeout_seconds,
        structured_output_max_retries=max_retries,
    )


def _build_validator(settings: Settings) -> StructuredOutputValidator:
    return StructuredOutputValidator(
        settings=settings,
        correction_prompt_builder=CorrectionPromptBuilder(),
    )


def _build_provider(
    *,
    settings: Settings | None = None,
    fake_client: _FakeAsyncClient,
) -> OllamaGemmaProvider:
    real_settings = settings or _build_settings()
    return OllamaGemmaProvider(
        settings=real_settings,
        validator=_build_validator(real_settings),
        http_client=fake_client,  # type: ignore[arg-type]  # test seam: FakeAsyncClient quacks like httpx.AsyncClient
    )


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://host.docker.internal:11434/api/generate")
    response = httpx.Response(status_code=status, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status}",
        request=request,
        response=response,
    )


async def test_generate_success_returns_generation_result() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[_FakeResponse(body={"response": '{"name":"Alice"}'})],
    )
    provider = _build_provider(fake_client=fake)

    result = await provider.generate("test prompt", _NAME_STRING_SCHEMA)

    assert result.data == {"name": "Alice"}
    assert result.attempts == 1
    assert result.raw_output == '{"name":"Alice"}'
    assert len(fake.post_calls) == 1


async def test_generate_sends_model_and_url_from_settings() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[_FakeResponse(body={"response": '{"name":"Alice"}'})],
    )
    settings = _build_settings(
        base_url="http://ollama.test:11434",
        model="gemma4:e4b",
    )
    provider = _build_provider(settings=settings, fake_client=fake)

    await provider.generate("hi", _NAME_STRING_SCHEMA)

    url, payload = fake.post_calls[0]
    assert url == "http://ollama.test:11434/api/generate"
    assert payload["model"] == "gemma4:e4b"
    assert payload["prompt"] == "hi"
    assert payload["stream"] is False


async def test_generate_strips_trailing_slash_from_base_url() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[_FakeResponse(body={"response": '{"name":"Alice"}'})],
    )
    settings = _build_settings(base_url="http://ollama.test:11434/")
    provider = _build_provider(settings=settings, fake_client=fake)

    await provider.generate("hi", _NAME_STRING_SCHEMA)

    url, _ = fake.post_calls[0]
    assert url == "http://ollama.test:11434/api/generate"


async def test_generate_connect_error_raises_intelligence_unavailable() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[httpx.ConnectError("connection refused")],
    )
    provider = _build_provider(fake_client=fake)

    with pytest.raises(IntelligenceUnavailableError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.cause, httpx.ConnectError)


async def test_generate_timeout_raises_intelligence_unavailable() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[httpx.TimeoutException("deadline exceeded")],
    )
    provider = _build_provider(fake_client=fake)

    with pytest.raises(IntelligenceUnavailableError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.cause, httpx.TimeoutException)


async def test_generate_http_500_raises_intelligence_unavailable() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[
            _FakeResponse(status_code=500, status_error=_http_status_error(500)),
        ],
    )
    provider = _build_provider(fake_client=fake)

    with pytest.raises(IntelligenceUnavailableError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.cause, httpx.HTTPStatusError)


async def test_generate_http_404_raises_intelligence_unavailable() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[
            _FakeResponse(status_code=404, status_error=_http_status_error(404)),
        ],
    )
    provider = _build_provider(fake_client=fake)

    with pytest.raises(IntelligenceUnavailableError):
        await provider.generate("hi", _NAME_STRING_SCHEMA)


async def test_generate_retries_on_bad_json_and_succeeds_on_second_attempt() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[
            _FakeResponse(body={"response": "not valid json at all"}),
            _FakeResponse(body={"response": '{"name":"Alice"}'}),
        ],
    )
    provider = _build_provider(fake_client=fake)

    result = await provider.generate("hi", _NAME_STRING_SCHEMA)

    assert result.attempts == 2
    assert result.data == {"name": "Alice"}
    assert len(fake.post_calls) == 2


def test_client_timeout_configured_from_settings() -> None:
    settings = _build_settings(timeout_seconds=12.5)
    provider = OllamaGemmaProvider(
        settings=settings,
        validator=_build_validator(settings),
    )
    try:
        assert provider.http_client.timeout.read == 12.5
    finally:
        # Sync path — we deliberately don't await aclose here; test-isolation
        # happens via the test harness not reusing the client.
        pass


async def test_aclose_closes_underlying_client() -> None:
    fake = _FakeAsyncClient(post_outcomes=[])
    provider = _build_provider(fake_client=fake)

    await provider.aclose()

    assert fake.aclose_calls == 1
    assert fake.is_closed is True


async def test_health_check_returns_true_on_200() -> None:
    fake = _FakeAsyncClient(
        get_outcomes=[_FakeResponse(body={"models": []})],
    )
    provider = _build_provider(fake_client=fake)

    assert await provider.health_check() is True
    assert fake.get_calls == ["http://host.docker.internal:11434/api/tags"]


async def test_health_check_returns_false_on_timeout() -> None:
    fake = _FakeAsyncClient(
        get_outcomes=[httpx.TimeoutException("deadline exceeded")],
    )
    provider = _build_provider(fake_client=fake)

    assert await provider.health_check() is False


async def test_health_check_returns_false_on_connect_error() -> None:
    fake = _FakeAsyncClient(
        get_outcomes=[httpx.ConnectError("refused")],
    )
    provider = _build_provider(fake_client=fake)

    assert await provider.health_check() is False


async def test_health_check_returns_false_on_http_error() -> None:
    fake = _FakeAsyncClient(
        get_outcomes=[
            _FakeResponse(status_code=500, status_error=_http_status_error(500)),
        ],
    )
    provider = _build_provider(fake_client=fake)

    assert await provider.health_check() is False


def test_provider_instance_satisfies_intelligence_provider_protocol() -> None:
    fake = _FakeAsyncClient(post_outcomes=[])
    provider = _build_provider(fake_client=fake)

    assert isinstance(provider, IntelligenceProvider)


# The `infer` tests below are SYNC pytest functions because `infer` calls
# `asyncio.run` internally. Running them as async would put them inside an
# already-running event loop and `asyncio.run` would raise RuntimeError — the
# same failure mode documented in the provider's module docstring.


def test_infer_yields_one_scored_output_per_prompt() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[
            _FakeResponse(body={"response": "first raw"}),
            _FakeResponse(body={"response": "second raw"}),
        ],
    )
    provider = _build_provider(fake_client=fake)

    results = list(provider.infer(["p1", "p2"]))

    assert len(results) == 2
    assert len(results[0]) == 1
    assert results[0][0].output == "first raw"
    assert results[0][0].score == 1.0
    assert results[1][0].output == "second raw"
    assert len(fake.post_calls) == 2


def test_infer_propagates_intelligence_unavailable_error_on_connect_failure() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[httpx.ConnectError("refused")],
    )
    provider = _build_provider(fake_client=fake)

    with pytest.raises(IntelligenceUnavailableError):
        list(provider.infer(["p1"]))

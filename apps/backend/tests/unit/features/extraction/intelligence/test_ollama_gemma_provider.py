"""Unit tests for OllamaGemmaProvider — the dual-interface Ollama plugin.

These tests exercise every method on the class at the unit level:
`async generate`, `async health_check`, `async aclose`, and the sync
`infer` path that LangExtract's orchestrator calls into. They never touch a
real Ollama, and the `infer` tests are sync pytest functions because `infer`
calls `asyncio.run` internally (an async test would cause `asyncio.run` to
raise `RuntimeError` because the event loop is already running — the exact
failure mode documented in the provider's module docstring).

The fake async client is hand-written in the same style as
`test_structured_output_validator.py`: no unittest.mock, no pytest-mock. Each
scripted response is a `_FakeResponse` instance or an exception to be raised
when `post` or `get` is awaited.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Self

import httpx
import pytest
from structlog.testing import capture_logs

from app.core.config import Settings
from app.exceptions import IntelligenceUnavailableError
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.features.extraction.intelligence.intelligence_provider import (
    IntelligenceProvider,
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

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()


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


def _patch_infer_client(
    monkeypatch: pytest.MonkeyPatch,
    post_outcomes: Sequence[_FakeResponse | BaseException],
) -> _FakeAsyncClient:
    """Patch ``httpx.AsyncClient`` in the provider module so ``infer()``'s
    fresh-client construction returns a ``_FakeAsyncClient`` with the given
    scripted responses. Returns the fake so callers can inspect ``post_calls``.
    """
    fake = _FakeAsyncClient(post_outcomes=post_outcomes)
    monkeypatch.setattr(
        "app.features.extraction.intelligence.ollama_gemma_provider.httpx.AsyncClient",
        lambda **_kwargs: fake,
    )
    return fake


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

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "connect_error"


async def test_generate_timeout_raises_intelligence_unavailable() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[httpx.TimeoutException("deadline exceeded")],
    )
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.__cause__, httpx.TimeoutException)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "timeout"


async def test_generate_http_500_raises_intelligence_unavailable() -> None:
    fake = _FakeAsyncClient(
        post_outcomes=[
            _FakeResponse(status_code=500, status_error=_http_status_error(500)),
        ],
    )
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.__cause__, httpx.HTTPStatusError)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "http_5xx"
    assert event["status"] == 500


async def test_generate_http_404_logs_http_4xx_cause() -> None:
    """4xx responses must log cause=http_4xx, not the generic http_5xx bucket.

    Regression guard: the provider previously logged cause=http_5xx for every
    HTTPStatusError regardless of actual status. A 404 (model not found), 401
    (proxy auth), or 400 (malformed request) would all be misreported as a
    server outage, burying the real operator signal.
    """
    fake = _FakeAsyncClient(
        post_outcomes=[
            _FakeResponse(status_code=404, status_error=_http_status_error(404)),
        ],
    )
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError):
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "http_4xx"
    assert event["status"] == 404


async def test_generate_http_400_logs_http_4xx_cause() -> None:
    """Lower 4xx statuses (e.g. bad request) also land in the http_4xx bucket."""
    fake = _FakeAsyncClient(
        post_outcomes=[
            _FakeResponse(status_code=400, status_error=_http_status_error(400)),
        ],
    )
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError):
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "http_4xx"
    assert event["status"] == 400


async def test_generate_http_503_logs_http_5xx_cause() -> None:
    """Upper 5xx statuses stay in the http_5xx bucket alongside 500."""
    fake = _FakeAsyncClient(
        post_outcomes=[
            _FakeResponse(status_code=503, status_error=_http_status_error(503)),
        ],
    )
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError):
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "http_5xx"
    assert event["status"] == 503


async def test_generate_raises_intelligence_unavailable_when_body_json_is_list() -> None:
    """Non-dict JSON bodies must not leak AttributeError.

    Regression guard: `response.json()` was typed as `dict[str, Any]`, but
    httpx happily decodes any valid JSON root including lists, strings, or
    numbers. A response like `[]` would trip the `body.get("response")` call
    with an AttributeError, bypassing the IntelligenceUnavailableError
    contract and returning an INTERNAL_ERROR 500 instead.
    """

    class _ListBodyResponse(_FakeResponse):
        def json(self) -> Any:  # type: ignore[override]
            return []

    fake = _FakeAsyncClient(post_outcomes=[_ListBodyResponse()])
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError):
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "invalid_json_shape"


async def test_generate_raises_intelligence_unavailable_when_body_json_is_string() -> None:
    """A bare-string JSON root also lands in the invalid_json_shape bucket."""

    class _StringBodyResponse(_FakeResponse):
        def json(self) -> Any:  # type: ignore[override]
            return "just a string"

    fake = _FakeAsyncClient(post_outcomes=[_StringBodyResponse()])
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError):
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "invalid_json_shape"


async def test_generate_raises_intelligence_unavailable_when_body_is_not_json() -> None:
    # Ollama (or an interposing proxy) returned a body that response.json()
    # cannot parse. This must map to IntelligenceUnavailableError, not crash
    # out of the provider as an unhandled JSONDecodeError.
    class _NonJsonResponse(_FakeResponse):
        def json(self) -> dict[str, Any]:  # type: ignore[override]
            msg = "Expecting value"
            raise json.JSONDecodeError(msg, "<html>proxy error</html>", 0)

    fake = _FakeAsyncClient(post_outcomes=[_NonJsonResponse()])
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.__cause__, json.JSONDecodeError)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "non_json_body"


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


async def test_client_timeout_configured_from_settings() -> None:
    settings = _build_settings(timeout_seconds=12.5)
    provider = OllamaGemmaProvider(
        settings=settings,
        validator=_build_validator(settings),
    )
    try:
        assert provider.http_client.timeout.read == 12.5
    finally:
        await provider.aclose()


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


def test_intelligence_unavailable_error_is_domain_error_subclass() -> None:
    from app.exceptions.base import DomainError

    assert issubclass(IntelligenceUnavailableError, DomainError)
    assert IntelligenceUnavailableError.code == "INTELLIGENCE_UNAVAILABLE"
    assert IntelligenceUnavailableError.http_status == 503


# The `infer` tests below are SYNC pytest functions because `infer` calls
# `asyncio.run` internally. Running them as async would put them inside an
# already-running event loop and `asyncio.run` would raise RuntimeError — the
# same failure mode documented in the provider's module docstring.


def test_infer_yields_one_scored_output_per_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_infer_client(
        monkeypatch,
        post_outcomes=[
            _FakeResponse(body={"response": '{"extractions":[{"a":1}]}'}),
            _FakeResponse(body={"response": '{"extractions":[{"b":2}]}'}),
        ],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    results = list(provider.infer(["p1", "p2"]))

    assert len(results) == 2
    assert len(results[0]) == 1
    assert results[0][0].score == 1.0
    # Output is the validator's normalized JSON (reserialized `data` dict),
    # not the raw model text — proving `infer` routes through the validator.
    assert json.loads(results[0][0].output) == {"extractions": [{"a": 1}]}
    assert json.loads(results[1][0].output) == {"extractions": [{"b": 2}]}
    assert len(fake.post_calls) == 2


def test_infer_routes_raw_text_through_structured_output_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fenced JSON must be cleaned by the validator's normalization step before
    # LangExtract's resolver sees it. If `infer` bypassed the validator, the
    # yielded output would still contain the ```json fence.
    fenced = '```json\n{"extractions":[]}\n```'
    _patch_infer_client(
        monkeypatch,
        post_outcomes=[_FakeResponse(body={"response": fenced})],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    results = list(provider.infer(["p1"]))

    assert "```" not in results[0][0].output
    assert json.loads(results[0][0].output) == {"extractions": []}


def test_infer_retries_on_wrapper_schema_violation_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First response is valid JSON but does NOT match the LangExtract wrapper
    # schema (`{"extractions": array}`). Validator retries; second response is
    # valid. Pins the contract that `infer` enforces the wrapper schema — not
    # just JSON parseability.
    fake = _patch_infer_client(
        monkeypatch,
        post_outcomes=[
            _FakeResponse(body={"response": '{"wrong":"shape"}'}),
            _FakeResponse(body={"response": '{"extractions":[]}'}),
        ],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    results = list(provider.infer(["p1"]))

    assert json.loads(results[0][0].output) == {"extractions": []}
    assert len(fake.post_calls) == 2


def test_infer_creates_fresh_http_client_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: issue #47 — ``infer()`` must create a fresh ``AsyncClient``
    inside the ``asyncio.run`` scope, not reuse the instance-level one.

    ``asyncio.run`` creates and then closes a fresh event loop each invocation.
    If the ``httpx.AsyncClient`` stored on the instance is reused across two
    ``asyncio.run`` calls, the second call finds the client's connection pool
    bound to the now-closed first loop and raises ``RuntimeError: Event loop is
    closed``. The fix is to create a fresh ``AsyncClient`` inside the
    event-loop scope so each ``infer()`` gets its own matched loop+client pair.

    This test pins the contract by intercepting ``httpx.AsyncClient``
    construction during ``infer()`` and counting instantiations. Before the
    fix, the count is zero (the instance-level client is reused). After the
    fix, each ``infer()`` call creates exactly one fresh client.
    """
    wrapper_body = {"response": '{"extractions":[]}'}
    construction_count = 0

    def _fake_async_client_factory(**_kwargs: Any) -> _FakeAsyncClient:
        nonlocal construction_count
        construction_count += 1
        return _FakeAsyncClient(  # type: ignore[return-value]  # test seam
            post_outcomes=[_FakeResponse(body=wrapper_body)],
        )

    monkeypatch.setattr(
        "app.features.extraction.intelligence.ollama_gemma_provider.httpx.AsyncClient",
        _fake_async_client_factory,
    )

    # Provider's own http_client was built before the patch; it is used only
    # by the `generate()`/`health_check()` async paths, not by `infer()`.
    fake = _FakeAsyncClient(post_outcomes=[])
    provider = _build_provider(fake_client=fake)

    construction_count = 0
    list(provider.infer(["p1"]))
    first_call_count = construction_count

    list(provider.infer(["p2"]))
    second_call_count = construction_count - first_call_count

    # Each infer() call must create exactly one fresh AsyncClient.
    assert first_call_count == 1, f"first infer() should create 1 client, got {first_call_count}"
    assert second_call_count == 1, f"second infer() should create 1 client, got {second_call_count}"


def test_infer_propagates_intelligence_unavailable_error_on_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_infer_client(
        monkeypatch,
        post_outcomes=[httpx.ConnectError("refused")],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    with pytest.raises(IntelligenceUnavailableError):
        list(provider.infer(["p1"]))


def test_infer_uses_a_single_asyncio_run_for_the_whole_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: `httpx.AsyncClient` binds its connection pool to the running
    # loop on first use. Calling `asyncio.run` per prompt would create a fresh
    # loop each iteration, leaving the shared client bound to a closed loop on
    # the second prompt. The fix batches all prompts into a single `asyncio.run`
    # — pinned here by counting invocations.
    import asyncio as _asyncio

    _patch_infer_client(
        monkeypatch,
        post_outcomes=[
            _FakeResponse(body={"response": '{"extractions":[]}'}),
            _FakeResponse(body={"response": '{"extractions":[]}'}),
            _FakeResponse(body={"response": '{"extractions":[]}'}),
        ],
    )

    call_count = 0
    real_run = _asyncio.run

    def counting_run(coro: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return real_run(coro, **kwargs)

    monkeypatch.setattr(
        "app.features.extraction.intelligence.ollama_gemma_provider.asyncio.run",
        counting_run,
    )

    provider = _build_provider(fake_client=_FakeAsyncClient())

    results = list(provider.infer(["p1", "p2", "p3"]))

    assert len(results) == 3
    assert call_count == 1


# The two constructor tests below pin the LangExtract plugin-instantiation
# path. `langextract.factory.create_model` calls `provider_class(**kwargs)`
# where `kwargs["model_id"]` is the model tag from `ModelConfig`. Any
# provider_kwargs a user supplies (model_url, format_type, …) come through
# the same dict. The provider must accept all of this without raising and
# must honor `model_id` as an override of `settings.ollama_model`.


async def test_init_accepts_langextract_model_id_kwarg() -> None:
    provider = OllamaGemmaProvider(model_id="gemma4:e2b")
    try:
        # Private attribute access is intentional — the constructor contract
        # is "honor model_id as the tag LangExtract will use on every POST".
        assert provider._model == "gemma4:e2b"  # noqa: SLF001 — test covers constructor contract
    finally:
        await provider.aclose()


async def test_init_absorbs_unknown_langextract_kwargs() -> None:
    # LangExtract's factory layers env-derived kwargs on top of user
    # provider_kwargs. The provider must not TypeError on unknown keys like
    # `base_url`, `format_type`, `timeout`, or `constraint` — those are
    # LangExtract/Ollama concerns that we deliberately do not consume.
    provider = OllamaGemmaProvider(
        model_id="gemma4:e2b",
        base_url="http://other-host:11434",
        format_type="json",
        timeout=7,
        constraint=None,
    )
    try:
        assert provider._model == "gemma4:e2b"  # noqa: SLF001 — test covers constructor contract
    finally:
        await provider.aclose()

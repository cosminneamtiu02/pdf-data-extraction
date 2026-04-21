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

import asyncio
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Self

import httpx
import pytest
from structlog.testing import capture_logs

from app.core.config import Settings
from app.exceptions import IntelligenceTimeoutError, IntelligenceUnavailableError
from app.exceptions._generated import IntelligenceTimeoutParams
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


async def test_generate_payload_includes_format_json_and_zero_temperature() -> None:
    """Regression guard for issue #136.

    The ``/api/generate`` payload must include ``format="json"`` so Ollama
    constrains output to valid JSON natively (reducing ``StructuredOutputValidator``
    retries for malformed text) and ``options={"temperature": 0}`` so generations
    are deterministic (repeatable retries, no drift between attempts). Before the
    fix, the payload carried only ``model``/``prompt``/``stream``, and every
    request paid the full formatting cost in validator retries.
    """
    fake = _FakeAsyncClient(
        post_outcomes=[_FakeResponse(body={"response": '{"name":"Alice"}'})],
    )
    provider = _build_provider(fake_client=fake)

    await provider.generate("hi", _NAME_STRING_SCHEMA)

    _, payload = fake.post_calls[0]
    assert payload["format"] == "json"
    assert isinstance(payload["options"], dict)
    assert payload["options"]["temperature"] == 0


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


async def test_generate_timeout_raises_intelligence_timeout() -> None:
    """Regression guard for issue #137.

    ``httpx.TimeoutException`` must map to ``IntelligenceTimeoutError`` (504),
    not ``IntelligenceUnavailableError`` (503). An Ollama request that exceeds
    the configured per-request timeout is a deadline violation — semantically
    distinct from connection failures (which are availability problems).
    """
    settings = _build_settings(timeout_seconds=7.5)
    fake = _FakeAsyncClient(
        post_outcomes=[httpx.TimeoutException("deadline exceeded")],
    )
    provider = _build_provider(settings=settings, fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceTimeoutError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.__cause__, httpx.TimeoutException)
    assert isinstance(excinfo.value.params, IntelligenceTimeoutParams)
    assert excinfo.value.params.budget_seconds == 7.5
    event = next(e for e in logs if e.get("event") == "intelligence_timeout")
    assert event["budget_seconds"] == 7.5


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


async def test_generate_read_error_raises_intelligence_unavailable() -> None:
    """httpx.ReadError (e.g. peer reset mid-stream) must not escape as HTTP 500.

    Regression guard for GitHub issue #49: only ConnectError and TimeoutException
    were caught, so broader RequestError subclasses like ReadError leaked through
    the domain error boundary.
    """
    fake = _FakeAsyncClient(
        post_outcomes=[
            httpx.ReadError("peer closed connection without sending complete message body")
        ],
    )
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.__cause__, httpx.ReadError)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "request_error"


async def test_generate_remote_protocol_error_raises_intelligence_unavailable() -> None:
    """httpx.RemoteProtocolError (e.g. malformed HTTP response) must not escape.

    Regression guard for GitHub issue #49.
    """
    fake = _FakeAsyncClient(
        post_outcomes=[httpx.RemoteProtocolError("malformed HTTP message")],
    )
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError) as excinfo:
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    assert isinstance(excinfo.value.__cause__, httpx.RemoteProtocolError)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "request_error"


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


async def test_generate_missing_response_field_surfaces_ollama_error_body() -> None:
    """Regression guard for issue #333.

    When Ollama (or an interposing proxy) returns a 200 whose body carries
    ``{"error": "model not loaded"}`` and no ``response`` field, the
    ``missing_response_field`` branch must include the error text in the
    structured log under an ``ollama_error`` key. Before the fix, the
    diagnostic was silently discarded — operators debugging failures saw
    ``cause=missing_response_field`` with no hint that Ollama sent back an
    explicit reason.
    """
    fake = _FakeAsyncClient(
        post_outcomes=[_FakeResponse(body={"error": "model not loaded"})],
    )
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError):
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "missing_response_field"
    assert event["ollama_error"] == "model not loaded"


async def test_generate_missing_response_field_without_error_key_omits_ollama_error() -> None:
    """When the body has neither ``response`` nor ``error``, the log line
    must still emit ``cause=missing_response_field`` but MUST NOT carry a
    stale or synthetic ``ollama_error`` value — the field is only present
    when Ollama actually sent one.
    """
    fake = _FakeAsyncClient(post_outcomes=[_FakeResponse(body={"done": True})])
    provider = _build_provider(fake_client=fake)

    with capture_logs() as logs, pytest.raises(IntelligenceUnavailableError):
        await provider.generate("hi", _NAME_STRING_SCHEMA)
    event = next(e for e in logs if e.get("event") == "intelligence_unavailable")
    assert event["cause"] == "missing_response_field"
    assert "ollama_error" not in event


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


async def test_health_check_returns_false_on_read_error() -> None:
    """ReadError during health check must return False, not crash.

    Regression guard for GitHub issue #49.
    """
    fake = _FakeAsyncClient(
        get_outcomes=[httpx.ReadError("peer reset")],
    )
    provider = _build_provider(fake_client=fake)

    assert await provider.health_check() is False


async def test_health_check_returns_false_on_remote_protocol_error() -> None:
    """RemoteProtocolError during health check must return False, not crash.

    Regression guard for GitHub issue #49.
    """
    fake = _FakeAsyncClient(
        get_outcomes=[httpx.RemoteProtocolError("malformed HTTP message")],
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


def test_infer_propagates_intelligence_timeout_error_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for issue #137 — timeout path on the ``infer()`` seam.

    ``infer()`` runs the fresh-client-per-asyncio.run path. A transport
    ``TimeoutException`` there must surface as ``IntelligenceTimeoutError``
    with the correct 504 budget, same as the ``generate()`` path.
    """
    _patch_infer_client(
        monkeypatch,
        post_outcomes=[httpx.TimeoutException("deadline exceeded")],
    )
    settings = _build_settings(timeout_seconds=9.25)
    provider = _build_provider(settings=settings, fake_client=_FakeAsyncClient())

    with pytest.raises(IntelligenceTimeoutError) as excinfo:
        list(provider.infer(["p1"]))
    assert isinstance(excinfo.value.params, IntelligenceTimeoutParams)
    assert excinfo.value.params.budget_seconds == 9.25


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


# ── LangExtract sampling-kwargs forwarding (issue #385) ────────────────
#
# Before the fix, ``infer(batch_prompts, **kwargs)`` absorbed and silently
# dropped every kwarg LangExtract forwarded. A caller setting
# ``temperature=0.7`` through ``lx.extract(..., language_model_params={
# "temperature": 0.7})`` got deterministic ``temperature=0`` output with no
# indication the override had been ignored. The fix threads a known
# allowlist of Ollama-options sampling keys (temperature, top_p, top_k,
# seed, num_ctx, num_predict, repeat_penalty, mirostat, mirostat_tau,
# mirostat_eta) from the ``infer`` kwargs into the ``/api/generate``
# ``options`` object, with caller-supplied values overriding defaults.
# Unknown kwargs are logged at DEBUG so operators see drift if LangExtract
# starts forwarding something new.


def test_infer_forwards_temperature_kwarg_into_payload_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``infer(temperature=0.7, ...)`` must post ``options.temperature == 0.7``.

    Before the fix, ``_build_payload`` hardcoded ``options={"temperature": 0}``
    and ignored every LangExtract-forwarded kwarg. A caller overriding the
    sampling temperature via LangExtract got deterministic output with no
    indication their override was dropped. This test pins the new contract:
    caller-supplied ``temperature`` wins over the provider default.
    """
    fake = _patch_infer_client(
        monkeypatch,
        post_outcomes=[_FakeResponse(body={"response": '{"extractions":[]}'})],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    list(provider.infer(["p1"], temperature=0.7))

    _, payload = fake.post_calls[0]
    assert payload["options"]["temperature"] == 0.7


def test_infer_forwards_multiple_sampling_kwargs_into_payload_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every sampling kwarg from the allowlist must land in payload.options.

    Pins the full allowlist surface (top_p, top_k, seed, num_ctx,
    num_predict, repeat_penalty, mirostat family) so silently dropping any
    one of them is a regression caught here rather than at runtime.
    """
    fake = _patch_infer_client(
        monkeypatch,
        post_outcomes=[_FakeResponse(body={"response": '{"extractions":[]}'})],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    list(
        provider.infer(
            ["p1"],
            temperature=0.9,
            top_p=0.95,
            top_k=40,
            seed=42,
            num_ctx=8192,
            num_predict=512,
            repeat_penalty=1.1,
            mirostat=2,
            mirostat_tau=5.0,
            mirostat_eta=0.1,
        )
    )

    _, payload = fake.post_calls[0]
    options = payload["options"]
    assert options["temperature"] == 0.9
    assert options["top_p"] == 0.95
    assert options["top_k"] == 40
    assert options["seed"] == 42
    assert options["num_ctx"] == 8192
    assert options["num_predict"] == 512
    assert options["repeat_penalty"] == 1.1
    assert options["mirostat"] == 2
    assert options["mirostat_tau"] == 5.0
    assert options["mirostat_eta"] == 0.1


def test_infer_without_kwargs_keeps_deterministic_temperature_zero_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no caller-supplied kwargs, ``options.temperature`` stays 0.

    This regression guard protects the issue #136 contract
    (``test_generate_payload_includes_format_json_and_zero_temperature``)
    for the ``infer()`` seam. The fix must add kwarg forwarding without
    changing the existing default sampling behavior for callers who
    forward nothing.
    """
    fake = _patch_infer_client(
        monkeypatch,
        post_outcomes=[_FakeResponse(body={"response": '{"extractions":[]}'})],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    list(provider.infer(["p1"]))

    _, payload = fake.post_calls[0]
    assert payload["options"]["temperature"] == 0
    assert payload["format"] == "json"


def test_infer_drops_non_sampling_kwargs_and_logs_them_at_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown kwargs must not leak into payload.options, and must be logged.

    LangExtract also forwards orchestrator kwargs like ``format_type``,
    ``constraint``, or ``model_url`` that are not Ollama sampling options.
    Those must not end up in the HTTP payload (they would confuse Ollama
    or leak unrelated state). The provider logs them at DEBUG under event
    ``ollama_provider_ignored_kwargs`` so operators see drift.
    """
    fake = _patch_infer_client(
        monkeypatch,
        post_outcomes=[_FakeResponse(body={"response": '{"extractions":[]}'})],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    with capture_logs() as logs:
        list(
            provider.infer(
                ["p1"],
                temperature=0.5,
                format_type="json",
                constraint=None,
                model_url="http://other:11434",
            )
        )

    _, payload = fake.post_calls[0]
    # Only the allowlisted sampling key landed in options.
    assert payload["options"]["temperature"] == 0.5
    assert "format_type" not in payload["options"]
    assert "constraint" not in payload["options"]
    assert "model_url" not in payload["options"]
    # And the ignored keys were logged so operators can see drift.
    event = next(
        (e for e in logs if e.get("event") == "ollama_provider_ignored_kwargs"),
        None,
    )
    assert event is not None, "ignored-kwargs must be logged for observability"
    ignored_keys = set(event["keys"])
    assert {"format_type", "constraint", "model_url"} <= ignored_keys
    assert "temperature" not in ignored_keys


def test_infer_allowlisted_kwarg_with_none_value_preserves_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowlisted kwarg whose value is ``None`` must not clobber the default.

    LangExtract orchestrator dicts may surface ``temperature=None`` (or
    ``seed=None``, …) when a caller wires a config field that is optional
    and left unset. Before the fix, ``_merge_sampling_options`` unconditionally
    copied the value over the default baseline, so the payload reached Ollama
    with ``options.temperature == null``. That breaks the determinism contract
    (issue #136) for ``temperature`` and risks a server-side 400 for keys
    Ollama validates as numeric (e.g. ``seed``). The contract is: treat an
    allowlisted key whose value is ``None`` as "not provided" — preserve the
    module-level default, do not forward ``null``.
    """
    fake = _patch_infer_client(
        monkeypatch,
        post_outcomes=[_FakeResponse(body={"response": '{"extractions":[]}'})],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    list(provider.infer(["p1"], temperature=None, seed=None))

    _, payload = fake.post_calls[0]
    # Default ``temperature=0`` preserved, ``seed`` not injected as ``None``.
    assert payload["options"]["temperature"] == 0
    assert "seed" not in payload["options"]


def test_infer_kwargs_propagate_across_every_prompt_in_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-call kwargs must apply to every prompt in the batch.

    ``infer`` iterates the batch inside a single ``asyncio.run`` scope; the
    sampling options the caller passed must decorate every POST, not just
    the first.
    """
    fake = _patch_infer_client(
        monkeypatch,
        post_outcomes=[
            _FakeResponse(body={"response": '{"extractions":[]}'}),
            _FakeResponse(body={"response": '{"extractions":[]}'}),
        ],
    )
    provider = _build_provider(fake_client=_FakeAsyncClient())

    list(provider.infer(["p1", "p2"], temperature=0.3, seed=7))

    assert len(fake.post_calls) == 2
    for _url, payload in fake.post_calls:
        assert payload["options"]["temperature"] == 0.3
        assert payload["options"]["seed"] == 7


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


# Cross-loop regression tests that use a real ``socketserver.ThreadingTCPServer``
# for issue #132 moved to the integration suite per issue #329 — binding a real
# socket on 127.0.0.1 violates the unit-level "no network" boundary codified by
# CLAUDE.md. See
# ``tests/integration/features/extraction/intelligence/test_ollama_gemma_provider_integration.py``
# for the migrated tests; they retain the bare-metal-HTTP-stub approach because
# ``respx`` intercepts above httpx's connection pool, which is exactly the
# per-loop binding surface those tests need to exercise.


# ── Loop-switch leak regression tests (issue #234) ─────────────────────
#
# Issue #234 pins two defensive invariants on ``_get_http_client``'s
# loop-switch rebuild path:
#
# 1. **No orphaned client on loop switch.** When the rebuild path fires, the
#    OUTGOING client must be ``aclose()``d. Before the fix, the code only
#    scheduled ``aclose()`` via ``run_coroutine_threadsafe`` when
#    ``old_loop.is_closed()`` was False; in the common test pattern where
#    the old loop is closed between calls, the scheduled branch is skipped
#    and the outgoing client leaks (only torn down eventually by GC with
#    an ``unclosed client`` warning).
#
# 2. **At most one new client per rebuild, even under concurrent entrants.**
#    Two coroutines reaching the rebuild branch on the same tick must
#    serialize on an ``asyncio.Lock`` so only the first allocates a fresh
#    ``httpx.AsyncClient``; the second sees the rebuilt state after the
#    first completes.
#
# These tests use a fake ``httpx.AsyncClient`` factory that counts
# constructions and ``aclose()`` invocations. They do NOT open real TCP
# connections — the cross-loop teardown mechanics are orthogonal to the
# invariants above, and mixing sockets in here would couple the tests to
# ``ThreadingTCPServer`` fixtures that already cover the socket path in
# the issue-#132 block above.


class _CountingFakeClient:
    """Fake ``httpx.AsyncClient`` that counts aclose calls and serves scripted posts.

    The monkey-patched factory below replaces ``httpx.AsyncClient`` inside
    the provider module so every construction the provider does in the
    rebuild path returns one of these. Tests then read ``.aclose_calls``
    to verify the outgoing client was awaited-close, not leaked.

    The fake also supports ``post()`` so callers can drive the full
    ``generate()`` path through it — the rebuild branch only fires inside
    async methods, and exercising ``generate()`` is the most-faithful way
    to prove the end-to-end behaviour (vs. poking the private seam).
    """

    def __init__(self, *, timeout: httpx.Timeout | None = None) -> None:
        self.timeout = timeout or httpx.Timeout(30.0)
        self.aclose_calls = 0
        self.is_closed = False
        self._post_body: dict[str, Any] = {"response": '{"name":"Alice"}'}

    async def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse:  # noqa: ARG002 — mirror httpx signature
        return _FakeResponse(body=self._post_body)

    async def aclose(self) -> None:
        self.aclose_calls += 1
        self.is_closed = True


def _patch_httpx_async_client_factory(
    monkeypatch: pytest.MonkeyPatch,
    built_clients: list[_CountingFakeClient],
) -> None:
    """Patch ``ollama_gemma_provider.httpx.AsyncClient`` to return counting fakes.

    Every construction the provider does — both the eager one in
    ``__init__`` and the rebuild path in ``_get_http_client`` — routes
    through this factory so the test sees every client the provider
    ever owns.
    """

    def _factory(**kwargs: Any) -> _CountingFakeClient:
        client = _CountingFakeClient(timeout=kwargs.get("timeout"))
        built_clients.append(client)
        return client

    monkeypatch.setattr(
        "app.features.extraction.intelligence.ollama_gemma_provider.httpx.AsyncClient",
        _factory,
    )


def test_generate_acloses_outgoing_client_when_old_loop_already_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: issue #234 — the outgoing client on a loop-switch rebuild
    must be ``aclose()``d even when the old event loop has already been
    closed.

    Before the fix, ``_get_http_client`` only scheduled ``aclose()`` on the
    old loop via ``run_coroutine_threadsafe`` when that loop was still
    alive. Tests that spin up sequential ``asyncio.new_event_loop()``s
    close the old loop before the next call, so the ``aclose()`` branch
    is skipped and the outgoing client is orphaned — the exact
    file-descriptor leak the issue flags. The fix awaits ``aclose()`` on
    the current loop as a fallback when the old loop is unreachable.
    """
    built_clients: list[_CountingFakeClient] = []
    _patch_httpx_async_client_factory(monkeypatch, built_clients)

    settings = _build_settings()
    provider = OllamaGemmaProvider(
        settings=settings,
        validator=_build_validator(settings),
    )
    # Provider's __init__ already built one fake via the auto-constructor
    # branch of ``self.http_client = http_client or httpx.AsyncClient(...)``.
    assert len(built_clients) == 1
    initial_client = built_clients[0]

    # First loop: drive a ``generate()`` so the provider binds
    # ``self.http_client`` to loop1.
    loop1 = asyncio.new_event_loop()
    try:
        first = loop1.run_until_complete(provider.generate("p1", _NAME_STRING_SCHEMA))
    finally:
        loop1.close()
    assert first.data == {"name": "Alice"}

    # Second loop: trigger the rebuild path. The old loop is already
    # closed. Under the BUG, ``old_loop.is_closed()`` is True so the
    # ``run_coroutine_threadsafe`` branch is skipped — the outgoing
    # client is never ``aclose()``d. Under the FIX, ``aclose()`` is
    # awaited on the current loop as a fallback.
    loop2 = asyncio.new_event_loop()
    try:
        second = loop2.run_until_complete(provider.generate("p2", _NAME_STRING_SCHEMA))
    finally:
        loop2.close()
    assert second.data == {"name": "Alice"}

    # After the switch there must be a second (fresh) client bound to
    # loop2 AND the first client must have been awaited-close.
    assert len(built_clients) == 2
    assert initial_client.aclose_calls == 1, (
        "outgoing client was not aclose()'d when the old loop was already closed — "
        "leak would accumulate across repeated loop switches"
    )


def test_generate_concurrent_loop_switch_builds_at_most_one_new_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: issue #234 — two concurrent entrants on the rebuild
    branch must serialize on a lock so only ONE fresh ``httpx.AsyncClient``
    is built per loop switch.

    The rebuild path, after the fix, ``await``s the outgoing client's
    ``aclose()``. That ``await`` is a scheduling point: without a guard
    lock, a sibling coroutine scheduled on the same loop could enter
    ``_get_http_client`` between the new-client assignment and the
    ``_http_client_loop`` update, see the stale loop reference, and build
    a SECOND fresh client. Only the second write is retained; the first
    is lost and never ``aclose()``d.

    This test pins the lock invariant by forcing both siblings to race
    through the rebuild path via ``asyncio.gather`` and asserting that
    exactly one new client was constructed during the concurrent window.
    """
    built_clients: list[_CountingFakeClient] = []
    _patch_httpx_async_client_factory(monkeypatch, built_clients)

    settings = _build_settings()
    provider = OllamaGemmaProvider(
        settings=settings,
        validator=_build_validator(settings),
    )
    # One client from __init__.
    assert len(built_clients) == 1

    # Bind to an initial loop so the second loop's rebuild branch fires.
    loop1 = asyncio.new_event_loop()
    try:
        loop1.run_until_complete(provider.generate("p1", _NAME_STRING_SCHEMA))
    finally:
        loop1.close()

    # Snapshot the pre-rebuild count so the assertion only counts NEW
    # clients built during the concurrent rebuild window, not the
    # earlier ones.
    pre_rebuild_count = len(built_clients)

    loop2 = asyncio.new_event_loop()
    try:

        async def _race() -> None:
            # Two concurrent generate() calls on the fresh loop. Under a
            # correctly-locked rebuild, exactly one allocates a new
            # client; the second waits on the lock, re-checks, and
            # shares the already-rebuilt instance.
            await asyncio.gather(
                provider.generate("p2", _NAME_STRING_SCHEMA),
                provider.generate("p3", _NAME_STRING_SCHEMA),
            )

        loop2.run_until_complete(_race())
    finally:
        loop2.close()

    new_clients_built = len(built_clients) - pre_rebuild_count
    assert new_clients_built == 1, (
        f"concurrent rebuild built {new_clients_built} clients; "
        "the asyncio.Lock guard must serialize the check-and-swap "
        "so only one fresh client is allocated per loop switch"
    )


def test_generate_does_not_hang_when_old_loop_stopped_but_not_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: PR #244 review feedback — the rebuild path must not
    hang when the old loop is not closed but is not running either (e.g.
    stopped via ``loop.stop()`` without a follow-up ``loop.close()``).

    Before the review fix, ``_aclose_stale_client`` branched on
    ``not old_loop.is_closed()`` and scheduled ``aclose()`` via
    ``run_coroutine_threadsafe``, then ``await``ed the wrapped future on
    the CURRENT loop. If the old loop was stopped-but-not-closed, nothing
    pulled the coroutine off its queue and the wrapped future never
    completed — the provider would block forever on the next
    ``generate()`` call after the stopped loop had been stamped onto
    ``_http_client_loop``.

    The fix narrows the schedule branch to ``is_running()`` and bounds
    the wait with ``asyncio.wait_for`` so even the residual case where
    ``is_running()`` is briefly true but the loop stalls afterwards
    falls back to a current-loop best-effort close within a few seconds.

    This test stamps a stopped-but-not-closed loop as
    ``_http_client_loop`` directly (rather than driving the rebuild via
    that loop) because pytest's main thread can only run one loop at a
    time; the invariant being pinned is that the rebuild path on the
    NEW loop does not submit-and-await on an unrunning old loop.
    """
    built_clients: list[_CountingFakeClient] = []
    _patch_httpx_async_client_factory(monkeypatch, built_clients)

    settings = _build_settings()
    provider = OllamaGemmaProvider(
        settings=settings,
        validator=_build_validator(settings),
    )
    assert len(built_clients) == 1
    initial_client = built_clients[0]

    # Construct an event loop that is neither running nor closed, then
    # stamp it on the provider as if an earlier ``generate()`` call had
    # bound the client to it. ``new_event_loop()`` returns a loop that
    # has never run — ``is_running()`` is False and ``is_closed()`` is
    # False, which is exactly the state this test needs.
    stopped_loop = asyncio.new_event_loop()
    try:
        assert not stopped_loop.is_closed()
        assert not stopped_loop.is_running()
        provider._http_client_loop = stopped_loop  # noqa: SLF001 — exercising the rebuild-path invariant

        # Drive ``generate()`` on a FRESH loop. The rebuild path fires
        # because ``_http_client_loop`` is a different (stopped) loop.
        # Under the BUG, ``run_coroutine_threadsafe(aclose(), stopped_loop)``
        # is submitted, nothing drains it, and ``asyncio.wrap_future(...)``
        # blocks forever. Under the FIX, the ``is_running()`` guard skips
        # the schedule path and runs ``aclose()`` on the current loop —
        # this call returns in well under the test's implicit timeout.
        new_loop = asyncio.new_event_loop()
        try:
            result = new_loop.run_until_complete(provider.generate("p1", _NAME_STRING_SCHEMA))
        finally:
            new_loop.close()
    finally:
        stopped_loop.close()

    assert result.data == {"name": "Alice"}
    # The outgoing client was still aclosed — via the current-loop
    # fallback branch — so no file descriptor leaks across the switch.
    assert initial_client.aclose_calls == 1, (
        "outgoing client was not aclose()'d via the current-loop fallback "
        "when the old loop was stopped-but-not-closed"
    )


# ── Stale-client aclose observability regression (issue #335) ─────────
#
# Before the fix, ``_aclose_stale_client``'s current-loop fallback
# swallowed ``aclose()`` failures at ``_logger.debug`` with only
# ``error=str(exc)`` attached. Operators running at the default
# ``WARNING`` level saw nothing when a real httpx internal error
# surfaced during teardown — the failure was invisible. The
# ``run_coroutine_threadsafe`` branch was even weaker: non-TimeoutError
# exceptions from ``aclose()`` scheduled on the old loop propagated up
# through ``_get_http_client`` instead of being captured as
# best-effort. Issue #335 pins both paths at ``warning`` with
# ``exc_info=True`` so the full traceback reaches operators while
# keeping the best-effort "don't crash on teardown" semantics.


class _RaisingAcloseFakeClient(_CountingFakeClient):
    """Counting fake whose ``aclose()`` raises a scripted exception.

    Subclass rather than a flag-on-the-base because the raising is the
    point of this fake — the base fake in the issue-#234 block tracks
    ``aclose_calls`` and returns happy. Mixing a raise into that class
    would muddle its invariant ("counts how many times the provider
    closed me") with this one ("surfaces a teardown failure to the
    provider's aclose handler").
    """

    def __init__(
        self,
        *,
        aclose_error: BaseException,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self._aclose_error = aclose_error

    async def aclose(self) -> None:
        self.aclose_calls += 1
        self.is_closed = True
        raise self._aclose_error


def test_stale_client_aclose_failure_logged_at_warning_with_exc_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: issue #335 — when the current-loop fallback in
    ``_aclose_stale_client`` catches a teardown error, it must log at
    ``warning`` with ``exc_info=True`` so operators see the failure.

    Before the fix, the fallback only logged at ``debug`` with
    ``error=str(exc)`` — invisible under the default ``WARNING`` log
    level and missing the traceback. The best-effort "don't crash on
    teardown" behaviour is preserved: the fresh client on the current
    loop remains valid and ``generate()`` still returns successfully.
    """
    settings = _build_settings()

    # Build the initial (soon-to-be-stale) client directly so we can
    # inject one that raises on aclose. We do NOT inject via the
    # constructor's ``http_client`` kwarg because that flips the
    # provider into the "caller owns loop affinity" branch of
    # ``_get_http_client`` (``self._injected_http_client is not
    # None``), which skips the rebuild path entirely. Instead, we
    # monkey-patch ``httpx.AsyncClient`` factory so the provider's
    # own constructor and rebuild allocations both go through us.
    built_clients: list[_CountingFakeClient] = []

    def _factory(**kwargs: Any) -> _CountingFakeClient:
        # First construction = the eagerly-built client in __init__
        # that we want to raise on aclose. Subsequent constructions =
        # the fresh rebuild client that should succeed so generate()
        # completes. Scripting per-call outcomes via a list avoids
        # global state and keeps the factory side-effect local.
        if not built_clients:
            client: _CountingFakeClient = _RaisingAcloseFakeClient(
                aclose_error=RuntimeError("Event loop is closed"),
                timeout=kwargs.get("timeout"),
            )
        else:
            client = _CountingFakeClient(timeout=kwargs.get("timeout"))
        built_clients.append(client)
        return client

    monkeypatch.setattr(
        "app.features.extraction.intelligence.ollama_gemma_provider.httpx.AsyncClient",
        _factory,
    )

    provider = OllamaGemmaProvider(
        settings=settings,
        validator=_build_validator(settings),
    )
    assert len(built_clients) == 1
    initial_client = built_clients[0]
    assert isinstance(initial_client, _RaisingAcloseFakeClient)

    # Stamp a pre-closed loop onto ``_http_client_loop`` so the
    # rebuild path's branch selector picks the current-loop fallback
    # (``not old_loop.is_running()`` AND ``old_loop.is_closed()``).
    # Creating and immediately closing a loop yields the exact state.
    stale_loop = asyncio.new_event_loop()
    stale_loop.close()
    assert stale_loop.is_closed()
    provider._http_client_loop = stale_loop  # noqa: SLF001 — exercising the rebuild-path invariant

    current_loop = asyncio.new_event_loop()
    try:
        with capture_logs() as logs:
            result = current_loop.run_until_complete(
                provider.generate("p1", _NAME_STRING_SCHEMA),
            )
    finally:
        current_loop.close()

    # Best-effort semantics preserved: the fresh client on the
    # current loop still served the request.
    assert result.data == {"name": "Alice"}
    assert initial_client.aclose_calls == 1, (
        "stale client's aclose() was not invoked on the current-loop "
        "fallback branch — test setup did not hit the target branch"
    )

    # The fix: the suppressed exception is logged at warning with
    # full traceback metadata. ``capture_logs()`` records
    # ``exc_info=True`` verbatim on the event dict because the
    # in-test processor chain does not rewrite it — asserting on
    # the flag is the stable contract here.
    aclose_warnings = [
        e
        for e in logs
        if e.get("event") == "stale_client_aclose_failed" and e.get("log_level") == "warning"
    ]
    assert len(aclose_warnings) == 1, (
        f"expected exactly one 'stale_client_aclose_failed' warning event; captured logs: {logs!r}"
    )
    event = aclose_warnings[0]
    assert event.get("exc_info") is True, (
        "warning must set ``exc_info=True`` so structlog renders the full "
        f"traceback into the 'exception' field for operators; got: {event!r}"
    )

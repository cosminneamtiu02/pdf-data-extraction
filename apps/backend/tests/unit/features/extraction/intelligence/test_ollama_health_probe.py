"""Unit tests for OllamaHealthProbe — the readiness probe for /ready.

Hand-written fake client in the same style as test_ollama_gemma_provider.py:
no unittest.mock, no pytest-mock. Each scripted response is a _FakeResponse
instance or an exception to be raised when ``get`` is awaited.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from app.features.extraction.intelligence.ollama_health_probe import (
    OllamaHealthProbe,
)

_EXPECTED_MODEL = "gemma4:e2b"

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal response stub with optional JSON body for /api/tags shape."""

    def __init__(
        self,
        *,
        body: dict[str, Any] | None = None,
        status_code: int = 200,
        status_error: httpx.HTTPStatusError | None = None,
    ) -> None:
        self._body = body
        self.status_code = status_code
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> Any:
        if self._body is None:
            # Mirror httpx's real behavior: body-less / non-JSON → JSONDecodeError.
            raise json.JSONDecodeError(msg="Expecting value", doc="", pos=0)
        return self._body


class _FakeAsyncClient:
    """Records ``get`` calls and replays scripted outcomes."""

    def __init__(
        self,
        get_outcomes: list[_FakeResponse | BaseException],
    ) -> None:
        self._get_outcomes = list(get_outcomes)
        self.get_calls: list[str] = []
        self.aclose_calls = 0

    async def get(self, url: str, **_kwargs: Any) -> _FakeResponse:
        self.get_calls.append(url)
        if not self._get_outcomes:
            pytest.fail("_FakeAsyncClient.get called more times than scripted")
        outcome = self._get_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        self.aclose_calls += 1


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        message=f"Server error {status}",
        request=httpx.Request("GET", "http://test/api/tags"),
        response=httpx.Response(status),
    )


def _tags_body(*names: str) -> dict[str, Any]:
    """Build an Ollama-shaped ``/api/tags`` body with the given model names."""
    return {"models": [{"name": name} for name in names]}


def _build_probe(
    fake_client: _FakeAsyncClient,
    *,
    tags_url: str = "http://host.docker.internal:11434/api/tags",
    expected_model: str = _EXPECTED_MODEL,
) -> OllamaHealthProbe:
    return OllamaHealthProbe(
        tags_url=tags_url,
        expected_model=expected_model,
        http_client=fake_client,  # type: ignore[arg-type]  # test seam: FakeAsyncClient quacks like httpx.AsyncClient
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_check_returns_true_when_expected_model_present() -> None:
    fake = _FakeAsyncClient(
        get_outcomes=[_FakeResponse(body=_tags_body(_EXPECTED_MODEL))],
    )
    probe = _build_probe(fake)

    assert await probe.check() is True
    assert fake.get_calls == ["http://host.docker.internal:11434/api/tags"]


async def test_check_returns_true_when_expected_model_among_many() -> None:
    fake = _FakeAsyncClient(
        get_outcomes=[
            _FakeResponse(body=_tags_body("llama3:8b", _EXPECTED_MODEL, "mistral:7b")),
        ],
    )
    probe = _build_probe(fake)

    assert await probe.check() is True


async def test_check_returns_false_when_expected_model_missing() -> None:
    fake = _FakeAsyncClient(
        get_outcomes=[_FakeResponse(body=_tags_body("llama3:8b", "mistral:7b"))],
    )
    probe = _build_probe(fake)

    with capture_logs() as logs:
        assert await probe.check() is False

    events = [entry.get("event") for entry in logs]
    assert "ollama_model_not_found" in events
    not_found_entry = next(
        entry for entry in logs if entry.get("event") == "ollama_model_not_found"
    )
    assert not_found_entry["url"] == "http://host.docker.internal:11434/api/tags"
    assert not_found_entry["status_code"] == 200
    assert not_found_entry["expected_model"] == _EXPECTED_MODEL
    assert not_found_entry["installed_models"] == ["llama3:8b", "mistral:7b"]


async def test_check_returns_false_when_models_list_empty() -> None:
    fake = _FakeAsyncClient(get_outcomes=[_FakeResponse(body=_tags_body())])
    probe = _build_probe(fake)

    assert await probe.check() is False


async def test_check_returns_false_when_body_missing_models_key() -> None:
    fake = _FakeAsyncClient(get_outcomes=[_FakeResponse(body={})])
    probe = _build_probe(fake)

    assert await probe.check() is False


async def test_check_returns_false_when_body_is_not_json() -> None:
    """If Ollama returns 200 but the body cannot be decoded as JSON, fail closed."""
    fake = _FakeAsyncClient(get_outcomes=[_FakeResponse(body=None)])
    probe = _build_probe(fake)

    with capture_logs() as logs:
        assert await probe.check() is False

    events = [entry.get("event") for entry in logs]
    assert "ollama_probe_invalid_json" in events


async def test_check_returns_false_on_connect_error() -> None:
    fake = _FakeAsyncClient(get_outcomes=[httpx.ConnectError("refused")])
    probe = _build_probe(fake)

    assert await probe.check() is False


async def test_check_returns_false_on_http_500() -> None:
    fake = _FakeAsyncClient(
        get_outcomes=[
            _FakeResponse(status_code=500, status_error=_http_status_error(500)),
        ],
    )
    probe = _build_probe(fake)

    assert await probe.check() is False


async def test_check_returns_false_on_timeout() -> None:
    fake = _FakeAsyncClient(
        get_outcomes=[httpx.TimeoutException("deadline exceeded")],
    )
    probe = _build_probe(fake)

    assert await probe.check() is False


async def test_aclose_closes_underlying_client() -> None:
    fake = _FakeAsyncClient(get_outcomes=[])
    probe = _build_probe(fake)

    await probe.aclose()

    assert fake.aclose_calls == 1

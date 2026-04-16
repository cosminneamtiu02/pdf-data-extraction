"""Unit tests for OllamaHealthProbe — the readiness probe for /ready.

Hand-written fake client in the same style as test_ollama_gemma_provider.py:
no unittest.mock, no pytest-mock. Each scripted response is a _FakeResponse
instance or an exception to be raised when ``get`` is awaited.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.features.extraction.intelligence.ollama_health_probe import (
    OllamaHealthProbe,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal response stub that supports ``raise_for_status``."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        body: dict[str, Any] | None = None,
        status_error: httpx.HTTPStatusError | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body or {}
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> dict[str, Any]:
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


def _build_probe(
    fake_client: _FakeAsyncClient,
    *,
    base_url: str = "http://host.docker.internal:11434",
) -> OllamaHealthProbe:
    return OllamaHealthProbe(
        base_url=base_url,
        http_client=fake_client,  # type: ignore[arg-type]  # test seam: FakeAsyncClient quacks like httpx.AsyncClient
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_check_returns_true_on_200() -> None:
    fake = _FakeAsyncClient(get_outcomes=[_FakeResponse(body={"models": []})])
    probe = _build_probe(fake)

    assert await probe.check() is True
    assert fake.get_calls == ["http://host.docker.internal:11434/api/tags"]


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

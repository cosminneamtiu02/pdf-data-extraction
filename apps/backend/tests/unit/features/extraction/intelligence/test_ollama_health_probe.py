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


async def test_aclose_does_not_close_externally_owned_client() -> None:
    """Probe must NOT ``aclose()`` a client it did not construct (issue #392).

    The production DI chain injects the ``OllamaGemmaProvider``'s
    ``http_client`` into the probe so both components share a single
    connection pool. The provider owns that client's lifespan; the probe
    must keep its hands off it. Without this invariant, the lifespan
    cleanup would double-close (probe first, then provider) and either
    race on the second close or — under a future refactor that shares the
    pool more widely — close sockets the provider still wants to use on a
    subsequent request.
    """
    fake = _FakeAsyncClient(get_outcomes=[])
    probe = _build_probe(fake)

    await probe.aclose()

    assert fake.aclose_calls == 0


async def test_probe_uses_injected_http_client_for_get() -> None:
    """The probe routes ``GET /api/tags`` through the injected client (issue #392).

    Pins that the probe does not surreptitiously construct its own
    ``httpx.AsyncClient`` when one is injected: the provider already owns a
    client bound to the same base URL, and the probe must reuse it to
    halve connection-pool / DNS / TLS cost under 1 Hz readiness polling.
    """
    fake = _FakeAsyncClient(
        get_outcomes=[_FakeResponse(body=_tags_body(_EXPECTED_MODEL))],
    )
    probe = _build_probe(fake)

    result = await probe.check()

    assert result is True
    # The single `get` call went through the injected fake, not some
    # freshly-constructed internal client — otherwise `get_calls` would be
    # empty and the outcome queue would still contain the scripted response.
    assert fake.get_calls == ["http://host.docker.internal:11434/api/tags"]


async def test_aclose_closes_internally_owned_client() -> None:
    """Probe owns and closes a client it constructed itself (issue #392).

    Preserves the default-construction path used by
    ``test_probe_aclose_is_idempotent`` and by any test harness that
    instantiates a standalone probe without wiring through the provider.
    When no ``http_client`` is injected, the probe constructs its own and
    is responsible for tearing it down on ``aclose()``.
    """
    probe = OllamaHealthProbe(
        tags_url="http://unused.example/api/tags",
        expected_model="unused",
    )

    # The probe built its own AsyncClient; verify it is open before close
    # and reports closed afterward. Reaches into the private attribute the
    # way the existing lifespan integration test does — this pins the
    # ownership contract rather than probing public behaviour.
    internal_client = probe._http_client  # noqa: SLF001 — pinning owned-client close contract
    assert internal_client.is_closed is False

    await probe.aclose()

    assert internal_client.is_closed is True

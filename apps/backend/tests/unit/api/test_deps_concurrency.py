"""Pin the concurrency-safe init contract for `app.api.deps` factories.

`get_structured_output_validator` and `get_intelligence_provider` are
called from FastAPI's request path, which runs concurrent requests on
the same `app.state` namespace. The check-then-set pattern

    if validator is None:
        validator = StructuredOutputValidator(...)
        request.app.state.structured_output_validator = validator

is a classic race: two threads can both observe `None`, both build an
instance, and only the second's gets stored — the first's is leaked.
For `OllamaGemmaProvider` the leaked instance also leaks an open
`httpx.AsyncClient` that lifespan cleanup will never see.

These tests widen the construction window with a brief sleep so the
race is deterministic, then assert that exactly one instance is built
under concurrent first-access.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.deps import (
    get_intelligence_provider,
    get_structured_output_validator,
)
from app.core.config import Settings
from app.features.extraction.intelligence.ollama_gemma_provider import (
    OllamaGemmaProvider,
)
from app.features.extraction.intelligence.structured_output_validator import (
    StructuredOutputValidator,
)
from app.main import create_app


def _settings(tmp_path: Path) -> Settings:
    skills = tmp_path / "invoice"
    skills.mkdir()
    (skills / "1.yaml").write_text(
        "name: invoice\n"
        "version: 1\n"
        "prompt: Extract header fields.\n"
        "examples:\n"
        "  - input: INV-1\n"
        "    output:\n"
        "      number: INV-1\n"
        "output_schema:\n"
        "  type: object\n"
        "  properties:\n"
        "    number:\n"
        "      type: string\n"
        "  required: [number]\n",
        encoding="utf-8",
    )
    return Settings(  # type: ignore[reportCallIssue]
        skills_dir=tmp_path,
    )


def test_get_structured_output_validator_constructs_only_once_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    construction_count = 0
    real_init = StructuredOutputValidator.__init__

    def counting_init(self: StructuredOutputValidator, *args: Any, **kwargs: Any) -> None:
        nonlocal construction_count
        construction_count += 1
        # Widen the race window so threads reliably overlap inside the
        # check-then-set body. Without this, threads serialize naturally
        # on cheap construction and the bug would not surface.
        time.sleep(0.05)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(StructuredOutputValidator, "__init__", counting_init)

    app = create_app(_settings(tmp_path))
    fake_request = SimpleNamespace(app=app)

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(
            ex.map(lambda _: get_structured_output_validator(fake_request), range(10))  # type: ignore[arg-type]
        )

    assert construction_count == 1, (
        f"validator was constructed {construction_count} times, expected 1 — "
        "the lazy init is racing under concurrent access"
    )
    # Every caller must observe the same instance.
    assert len({id(r) for r in results}) == 1


def test_get_intelligence_provider_constructs_only_once_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Same race shape as the validator test, on the provider factory.

    The provider's `__init__` would normally also open an `httpx.AsyncClient`
    — we monkeypatch the entire construction path with a lightweight stub so
    the test stays a true unit test (no network resources, no event loop)
    and so a leaked construction does not leave open clients behind.
    """
    construction_count = 0

    def stub_init(_self: OllamaGemmaProvider, *_args: Any, **_kwargs: Any) -> None:
        nonlocal construction_count
        construction_count += 1
        # Widen the race window. With the bug, threads observe state.None,
        # all sleep, all construct, last write wins. Skip the real
        # `OllamaGemmaProvider.__init__` entirely — this unit test
        # exercises the deps-factory critical section, not httpx.
        time.sleep(0.05)

    monkeypatch.setattr(OllamaGemmaProvider, "__init__", stub_init)

    app = create_app(_settings(tmp_path))
    fake_request = SimpleNamespace(app=app)

    with ThreadPoolExecutor(max_workers=10) as ex:
        providers = list(
            ex.map(lambda _: get_intelligence_provider(fake_request), range(10))  # type: ignore[arg-type]
        )

    assert construction_count == 1, (
        f"provider was constructed {construction_count} times, expected 1 — "
        "the lazy init is racing under concurrent access"
    )
    assert len({id(p) for p in providers}) == 1

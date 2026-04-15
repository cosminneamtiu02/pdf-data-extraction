"""Integration tests: `create_app(settings=...)` must flow through to deps.

Pins the contract that `create_app`'s explicit `settings` argument — the
documented seam for integration tests that need custom config without
mutating process-wide env vars — reaches the FastAPI `Depends(get_settings)`
dependency when a request is handled. Historically `get_settings` was
`@lru_cache`'d at module level, so a test that built an app with custom
settings could still see default/env settings at the dependency site. The
fix routes all three DI factories through `request.app.state`.
"""

from pathlib import Path
from typing import Annotated

from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from app.api.deps import (
    get_intelligence_provider,
    get_settings,
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


def _settings_with_unique_cors(tmp_path: Path) -> Settings:
    # pydantic-settings loads remaining fields from env / defaults.
    (tmp_path / "invoice").mkdir()
    (tmp_path / "invoice" / "1.yaml").write_text(
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
        cors_origins=["http://unique.example"],
    )


async def test_get_settings_dep_returns_settings_passed_to_create_app(tmp_path: Path) -> None:
    custom = _settings_with_unique_cors(tmp_path)
    app = create_app(custom)
    captured: list[Settings] = []

    @app.get("/_probe_settings")
    async def probe(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, list[str]]:
        captured.append(settings)
        return {"cors_origins": list(settings.cors_origins)}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/_probe_settings")

    assert response.status_code == 200
    assert response.json() == {"cors_origins": ["http://unique.example"]}
    assert captured[0] is custom


async def test_get_validator_dep_uses_app_state_settings(tmp_path: Path) -> None:
    custom = _settings_with_unique_cors(tmp_path)
    app = create_app(custom)
    captured: list[StructuredOutputValidator] = []

    @app.get("/_probe_validator")
    async def probe(
        validator: Annotated[StructuredOutputValidator, Depends(get_structured_output_validator)],
    ) -> dict[str, bool]:
        captured.append(validator)
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.get("/_probe_validator")

    # The validator binds Settings at construction time; the dep must hand us
    # one whose Settings is the custom instance, not a default-env build.
    assert captured[0]._settings is custom  # noqa: SLF001 — covers dep wiring


async def test_get_intelligence_provider_dep_uses_app_state_settings(tmp_path: Path) -> None:
    custom = _settings_with_unique_cors(tmp_path)
    app = create_app(custom)
    captured: list[OllamaGemmaProvider] = []

    @app.get("/_probe_provider")
    async def probe(
        provider: Annotated[OllamaGemmaProvider, Depends(get_intelligence_provider)],
    ) -> dict[str, bool]:
        captured.append(provider)
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.get("/_probe_provider")
        # Second request must get the SAME provider instance (app-scoped cache).
        await client.get("/_probe_provider")

    assert len(captured) == 2
    assert captured[0] is captured[1]
    # And the provider's internal validator was built from the custom
    # Settings, not a default-env instance.
    assert captured[0]._validator._settings is custom  # noqa: SLF001 — covers dep wiring

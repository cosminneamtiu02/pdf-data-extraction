"""Integration test: `get_correction_prompt_builder` must be per-call, not cached.

Pins the contract that `get_correction_prompt_builder` in `app/api/deps.py`
does not memoize its return value with `@lru_cache`. The module docstring
explicitly forbids module-level `lru_cache` on factories because the cache
hands every caller — including every `create_app()` in the same pytest
process — the same instance, silently bleeding state between apps and
breaking per-app overrides that the docstring-documented test seam depends
on.

The regression this test pins: two independently built apps must resolve
their `CorrectionPromptBuilder` through separate factory invocations, so
that if the builder ever gains per-app state (e.g. via a Settings argument),
the apps remain isolated. With `@lru_cache(maxsize=1)` on the factory, the
two apps would receive the exact same instance by identity. Issue #148.
"""

from typing import Annotated

from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_correction_prompt_builder
from app.features.extraction.intelligence.correction_prompt_builder import (
    CorrectionPromptBuilder,
)
from app.main import create_app


async def test_get_correction_prompt_builder_not_shared_across_apps() -> None:
    """Two `create_app()` instances must each call the factory independently.

    `@lru_cache(maxsize=1)` would serve the same memoized instance to both
    apps by identity. Removing it makes the factory a plain function, so
    each call returns a fresh builder and per-app overrides remain honest.
    """
    first_app = create_app()
    second_app = create_app()

    first_captured: list[CorrectionPromptBuilder] = []
    second_captured: list[CorrectionPromptBuilder] = []

    @first_app.get("/_probe_builder")
    async def first_probe(
        builder: Annotated[
            CorrectionPromptBuilder,
            Depends(get_correction_prompt_builder),
        ],
    ) -> dict[str, bool]:
        first_captured.append(builder)
        return {"ok": True}

    @second_app.get("/_probe_builder")
    async def second_probe(
        builder: Annotated[
            CorrectionPromptBuilder,
            Depends(get_correction_prompt_builder),
        ],
    ) -> dict[str, bool]:
        second_captured.append(builder)
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=first_app),
        base_url="http://test",
    ) as client:
        first_response = await client.get("/_probe_builder")
    async with AsyncClient(
        transport=ASGITransport(app=second_app),
        base_url="http://test",
    ) as client:
        second_response = await client.get("/_probe_builder")

    # Assert the routes actually ran before indexing `captured` lists.
    assert first_response.status_code == 200
    assert second_response.status_code == 200

    # Each app must resolve its own builder via a fresh factory call.
    # `@lru_cache(maxsize=1)` would make these the same object by identity.
    assert first_captured[0] is not second_captured[0]


async def test_get_correction_prompt_builder_honors_dependency_override() -> None:
    """`app.dependency_overrides` must bind the factory to a test-supplied stub.

    This is the primary test-seam contract the module docstring protects:
    an integration test installing an override before the first request
    must see that override at the dependency site. Orthogonal to the
    cross-app-sharing regression above, this pins the override path itself.
    """
    app = create_app()
    stub = CorrectionPromptBuilder()
    app.dependency_overrides[get_correction_prompt_builder] = lambda: stub

    captured: list[CorrectionPromptBuilder] = []

    @app.get("/_probe_builder")
    async def probe(
        builder: Annotated[
            CorrectionPromptBuilder,
            Depends(get_correction_prompt_builder),
        ],
    ) -> dict[str, bool]:
        captured.append(builder)
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/_probe_builder")

    assert response.status_code == 200
    assert captured[0] is stub

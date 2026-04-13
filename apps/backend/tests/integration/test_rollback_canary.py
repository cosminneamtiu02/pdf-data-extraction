"""Canary tests proving test isolation via table truncation between tests.

Part 1 inserts a widget. Part 2 verifies the widget is gone (proving
_cleanup_tables truncated between tests).
"""

from httpx import AsyncClient


async def test_data_isolation_canary_insert(client: AsyncClient):
    """Insert a widget via the API — part 1 of the isolation canary."""
    response = await client.post(
        "/api/v1/widgets",
        json={"name": "canary-widget"},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "canary-widget"


async def test_data_isolation_canary_verify_clean(client: AsyncClient):
    """Verify the widget from part 1 is gone — proves truncation works."""
    response = await client.get("/api/v1/widgets")
    assert response.status_code == 200
    items = response.json()["items"]
    canary_names = [w["name"] for w in items if w["name"] == "canary-widget"]
    assert canary_names == [], "canary-widget should have been truncated between tests"

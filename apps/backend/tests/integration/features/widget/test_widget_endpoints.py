"""Integration tests for Widget CRUD endpoints."""

import uuid

from httpx import AsyncClient


async def test_create_widget_persists_and_returns(client: AsyncClient):
    """POST /api/v1/widgets should create and return a WidgetRead."""
    response = await client.post(
        "/api/v1/widgets",
        json={"name": "Integration Widget", "description": "A test widget"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Integration Widget"
    assert body["description"] == "A test widget"
    assert "id" in body
    assert "created_at" in body
    assert "updated_at" in body


async def test_get_widget_returns_when_exists(client: AsyncClient):
    """GET /api/v1/widgets/{id} should return the widget."""
    create = await client.post("/api/v1/widgets", json={"name": "Get-Test"})
    widget_id = create.json()["id"]

    response = await client.get(f"/api/v1/widgets/{widget_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Get-Test"


async def test_get_widget_returns_404_with_code_and_params(client: AsyncClient):
    """GET /api/v1/widgets/{id} should return 404 with WIDGET_NOT_FOUND."""
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/v1/widgets/{fake_id}")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "WIDGET_NOT_FOUND"
    assert body["error"]["params"]["widget_id"] == fake_id


async def test_list_widgets_returns_paginated(client: AsyncClient):
    """GET /api/v1/widgets should return a paginated list."""
    # Create a few widgets
    for _i in range(3):
        await client.post("/api/v1/widgets", json={"name": f"List-Test-{uuid.uuid4()}"})

    response = await client.get("/api/v1/widgets?page=1&size=2")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert "page" in body
    assert "size" in body
    assert "pages" in body
    assert len(body["items"]) <= 2
    assert body["page"] == 1
    assert body["size"] == 2


async def test_list_widgets_returns_empty_page(client: AsyncClient):
    """GET /api/v1/widgets should return empty page with high page number."""
    response = await client.get("/api/v1/widgets?page=9999&size=20")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []


async def test_patch_widget_updates_and_bumps_updated_at(client: AsyncClient):
    """PATCH /api/v1/widgets/{id} should update fields and bump updated_at."""
    create = await client.post("/api/v1/widgets", json={"name": f"Patch-Test-{uuid.uuid4()}"})
    widget = create.json()

    response = await client.patch(
        f"/api/v1/widgets/{widget['id']}",
        json={"name": "Patched Name"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Patched Name"
    from datetime import datetime

    created_ts = datetime.fromisoformat(widget["updated_at"])
    updated_ts = datetime.fromisoformat(updated["updated_at"])
    assert updated_ts >= created_ts


async def test_patch_widget_returns_404_when_missing(client: AsyncClient):
    """PATCH /api/v1/widgets/{id} should return 404 when widget doesn't exist."""
    fake_id = str(uuid.uuid4())
    response = await client.patch(
        f"/api/v1/widgets/{fake_id}",
        json={"name": "Nope"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WIDGET_NOT_FOUND"


async def test_delete_widget_removes(client: AsyncClient):
    """DELETE /api/v1/widgets/{id} should remove the widget."""
    create = await client.post("/api/v1/widgets", json={"name": f"Delete-Test-{uuid.uuid4()}"})
    widget_id = create.json()["id"]

    response = await client.delete(f"/api/v1/widgets/{widget_id}")
    assert response.status_code == 204

    # Verify it's gone
    get_response = await client.get(f"/api/v1/widgets/{widget_id}")
    assert get_response.status_code == 404


async def test_delete_widget_returns_404_when_missing(client: AsyncClient):
    """DELETE /api/v1/widgets/{id} should return 404 when widget doesn't exist."""
    fake_id = str(uuid.uuid4())
    response = await client.delete(f"/api/v1/widgets/{fake_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "WIDGET_NOT_FOUND"


async def test_create_widget_duplicate_name_returns_409(client: AsyncClient):
    """POST with duplicate name should return 409 WIDGET_NAME_CONFLICT."""
    unique_name = f"Unique-{uuid.uuid4()}"
    await client.post("/api/v1/widgets", json={"name": unique_name})
    response = await client.post("/api/v1/widgets", json={"name": unique_name})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WIDGET_NAME_CONFLICT"
    assert response.json()["error"]["params"]["name"] == unique_name


async def test_create_widget_empty_name_returns_422(client: AsyncClient):
    """POST with empty body should return 422 VALIDATION_FAILED."""
    response = await client.post("/api/v1/widgets", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_create_widget_name_too_long_returns_422_with_widget_name_too_long_code(
    client: AsyncClient,
):
    """POST with name > 255 chars should return 422 WIDGET_NAME_TOO_LONG."""
    long_name = "x" * 256
    response = await client.post("/api/v1/widgets", json={"name": long_name})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "WIDGET_NAME_TOO_LONG"
    assert body["error"]["params"]["max_length"] == 255
    assert body["error"]["params"]["actual_length"] == 256


async def test_all_responses_include_request_id(client: AsyncClient):
    """Every response should have X-Request-ID header."""
    response = await client.get("/api/v1/widgets")
    assert "X-Request-ID" in response.headers


async def test_error_response_includes_request_id_in_body(client: AsyncClient):
    """Error responses should include request_id in the error body."""
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/v1/widgets/{fake_id}")
    body = response.json()
    assert "request_id" in body["error"]
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]

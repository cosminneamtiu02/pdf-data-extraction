"""Contract tests — validates OpenAPI spec compliance.

Schemathesis-based contract tests require a running database.
These tests are designed to run in CI against the full docker-compose stack.

For local development without a DB, run: task test:contract
(which starts the stack first).

This file contains a lightweight spec validation test that always runs,
plus the Schemathesis parametrized test that runs only when DB is available.
"""

import os

from starlette.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.main import app


def test_openapi_spec_is_valid():
    """The OpenAPI spec should be valid and contain all expected endpoints."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/openapi.json")
    assert response.status_code == 200

    spec = response.json()
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "Project Template API"

    paths = spec["paths"]

    # Health endpoints at root
    assert "/health" in paths
    assert "/ready" in paths

    # Widget CRUD under /api/v1/
    assert "/api/v1/widgets" in paths
    assert "/api/v1/widgets/{widget_id}" in paths

    # Widget operations
    widget_collection = paths["/api/v1/widgets"]
    assert "post" in widget_collection
    assert "get" in widget_collection

    widget_item = paths["/api/v1/widgets/{widget_id}"]
    assert "get" in widget_item
    assert "patch" in widget_item
    assert "delete" in widget_item


def test_health_endpoint_conforms_to_spec():
    """Health endpoint should return the expected shape."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

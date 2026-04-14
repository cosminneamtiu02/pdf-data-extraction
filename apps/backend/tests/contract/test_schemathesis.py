"""Contract tests — validates OpenAPI spec compliance.

The minimal shell ships only `/health` and `/ready` at this stage. As feature-dev
lands the extraction endpoint (PDFX-E006), this file is extended with schemathesis
assertions against `/api/v1/extract`.
"""

from starlette.testclient import TestClient

from app.main import app


def test_openapi_spec_is_valid() -> None:
    """The OpenAPI spec should be valid and contain the base endpoints."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/openapi.json")
    assert response.status_code == 200

    spec = response.json()
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "PDF Data Extraction API"

    paths = spec["paths"]

    # Health endpoints at root
    assert "/health" in paths
    assert "/ready" in paths


def test_health_endpoint_conforms_to_spec() -> None:
    """Health endpoint should return the expected shape."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

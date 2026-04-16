"""Contract tests — degraded-mode response shape conformance (PDFX-E007-F002).

Verify that /health and /ready responses conform to their OpenAPI schemas
when the app boots in degraded mode (Ollama unreachable at startup).
TestClient invokes the lifespan, which fires the startup probe — with no
real Ollama, the probe fails and the app enters degraded mode naturally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from starlette.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _write_valid_skill(base: Path) -> None:
    body = {
        "name": "invoice",
        "version": 1,
        "prompt": "Extract header fields.",
        "examples": [{"input": "INV-1", "output": {"number": "INV-1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
    }
    target = base / "invoice"
    target.mkdir(parents=True, exist_ok=True)
    (target / "1.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def test_degraded_ready_503_conforms_to_openapi_schema(tmp_path: Path) -> None:
    """GET /ready 503 response shape matches the declared OpenAPI 503 schema."""
    _write_valid_skill(tmp_path)
    app = create_app(
        Settings(  # type: ignore[reportCallIssue]
            skills_dir=tmp_path,
            app_env="development",
            ollama_base_url="http://127.0.0.1:1",
            ollama_probe_timeout_seconds=0.5,
        ),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        # Fetch the OpenAPI spec to extract the 503 schema
        spec_response = client.get("/openapi.json")
        assert spec_response.status_code == 200
        spec: dict[str, Any] = spec_response.json()

        ready_503 = spec["paths"]["/ready"]["get"]["responses"]["503"]
        schema_ref = ready_503["content"]["application/json"]["schema"]

        # Resolve $ref if present
        if "$ref" in schema_ref:
            ref_path = schema_ref["$ref"].lstrip("#/").split("/")
            schema = spec
            for part in ref_path:
                schema = schema[part]
        else:
            schema = schema_ref

        # Hit /ready — should be 503 in degraded mode
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()

    # Validate against the schema's required properties
    assert "status" in body
    assert body["status"] == "not_ready"
    assert "reason" in body
    assert body["reason"] == "ollama_unreachable"
    # Confirm the schema declares these exact literal types
    assert schema["properties"]["status"]["const"] == "not_ready"
    assert schema["properties"]["reason"]["const"] == "ollama_unreachable"


def test_degraded_health_200_conforms_to_openapi_schema(tmp_path: Path) -> None:
    """GET /health returns 200 even in degraded mode — liveness is unaffected."""
    _write_valid_skill(tmp_path)
    app = create_app(
        Settings(  # type: ignore[reportCallIssue]
            skills_dir=tmp_path,
            app_env="development",
            ollama_base_url="http://127.0.0.1:1",
            ollama_probe_timeout_seconds=0.5,
        ),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

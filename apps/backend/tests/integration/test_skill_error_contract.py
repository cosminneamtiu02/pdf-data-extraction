"""Integration tests for PDFX-E002-F003 — skill-lookup error contract.

Verifies:
- `create_app()` logs `skill_validation_failed` at `critical` with structured
  `file` and `reason` fields before re-raising.
- `SkillNotFoundError` propagating from a request handler is serialized by the
  exception-handler middleware into the `ErrorResponse` envelope with
  `error.code == "SKILL_NOT_FOUND"`, status 404, and the raised params intact.
"""

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.exceptions import SkillNotFoundError, SkillValidationFailedError
from app.main import create_app

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _settings_with_skills(skills_dir: Path) -> Settings:
    # Pin `app_env` so `configure_logging` always picks the key-value
    # renderer; otherwise a stray `APP_ENV=production` in the runner env
    # flips logging to JSON and breaks the `file=` / `reason=` substring
    # assertions below.
    return Settings(skills_dir=skills_dir, app_env="development")  # type: ignore[reportCallIssue]


async def test_create_app_logs_critical_on_skill_validation_failed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "nowhere"

    with pytest.raises(SkillValidationFailedError):
        create_app(_settings_with_skills(missing))

    captured = _ANSI.sub("", capsys.readouterr().out)
    assert "skill_validation_failed" in captured
    assert "critical" in captured.lower()
    assert "nowhere" in captured
    assert "file=" in captured
    assert "reason=" in captured


async def test_skill_not_found_serializes_as_404_envelope(tmp_path: Path) -> None:
    """A `SkillNotFoundError` raised from a route serializes through the
    exception handler as a 404 `ErrorResponse` envelope.

    We mount an ad-hoc test route rather than hitting `/api/v1/extract`
    (which does not yet exist — PDFX-E006-F003) because the scenario's
    subject is the handler wiring, not any specific route implementation.
    """
    _write_valid_skill(tmp_path)
    app = create_app(_settings_with_skills(tmp_path))

    async def _boom() -> None:
        raise SkillNotFoundError(name="mystery", version="1")

    app.add_api_route("/_test/skill-not-found", _boom, methods=["GET"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test/skill-not-found")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "SKILL_NOT_FOUND"
    assert body["error"]["params"] == {"name": "mystery", "version": "1"}
    assert body["error"]["details"] is None
    assert "request_id" in body["error"]


def _write_valid_skill(base: Path) -> None:
    import yaml

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

"""Integration tests for PDFX-E002-F003 — skill-lookup error contract.

Verifies:
- `create_app()` logs `skill_validation_failed` at `critical` with structured
  `file` and `reason` fields before re-raising.
- `SkillNotFoundError` propagating from a request handler is serialized by the
  exception-handler middleware into the `ErrorResponse` envelope with
  `error.code == "SKILL_NOT_FOUND"`, status 404, and the raised params intact.
"""

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.core.config import Settings
from app.exceptions import SkillNotFoundError, SkillValidationFailedError
from app.main import create_app


def _settings_with_skills(skills_dir: Path) -> Settings:
    return Settings(skills_dir=skills_dir, app_env="development")  # type: ignore[reportCallIssue]


async def test_create_app_logs_critical_on_skill_validation_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_app()`` must log a structured ``skill_validation_failed`` event
    at ``critical`` with ``file`` and ``reason`` kwargs before re-raising.

    Spies directly on ``app.main._logger`` rather than using
    ``structlog.testing.capture_logs`` or ``capsys``: both depend on
    structlog's runtime state, which is reset by ``create_app()`` itself
    when it calls ``configure_logging``. The previous ``capsys``-based
    assertions against rendered output (``"file=" in captured.out``) were
    brittle because a renderer change (``KeyValueRenderer`` →
    ``ConsoleRenderer``) or a key rename (``file=`` → ``path=``) would
    silently break them even when the underlying code was correct. A plain
    attribute-swap is deterministic — it records the raw event name and
    kwargs the code emitted, independent of the renderer and regardless of
    whether the session has already been configured. Issue #281.
    """
    critical_events: list[tuple[str, dict[str, Any]]] = []

    class _SpyLogger:
        def critical(self, event: str, **kwargs: Any) -> None:
            critical_events.append((event, kwargs))

        def info(self, _event: str, **_kwargs: Any) -> None:
            pass

        def warning(self, _event: str, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(main_module, "_logger", _SpyLogger())

    missing = tmp_path / "nowhere"

    with pytest.raises(SkillValidationFailedError):
        create_app(_settings_with_skills(missing))

    matching = [
        (event, kwargs) for event, kwargs in critical_events if event == "skill_validation_failed"
    ]
    assert len(matching) == 1, (
        f"Expected exactly one skill_validation_failed event, got {critical_events}"
    )
    _, kwargs = matching[0]
    assert kwargs["file"] is not None
    assert str(missing) in str(kwargs["file"])
    assert kwargs["reason"] is not None
    # exc_info=True is load-bearing: it attaches the underlying traceback so
    # operators can triage the cause without tailing a second log line.
    assert kwargs.get("exc_info") is True


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

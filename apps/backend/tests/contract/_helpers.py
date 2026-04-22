"""Shared contract-test helpers.

Both `test_extract_contract.py` and `test_schemathesis.py` need the same
three fixtures to drive `POST /api/v1/extract`: a valid skill YAML on
disk, a `Settings` instance that points at it (with `app_env=development`
so `/openapi.json` is exposed), and a canned `ExtractionResult` for the
200 happy path. Keeping one definition here instead of two copies means
a contract-envelope change lands in one place; the two test files drift
less as the spec evolves.

The canned-result builder itself (`make_canned_result`) lives in
``tests/_support/extraction_fixtures`` so the contract layer, the
``/extract`` integration suite, and the benchmark integration suite all
consume a single parametrized helper (issue #404). It is re-exported
here so existing contract-test imports (``from tests.contract._helpers
import make_canned_result``) keep working without a second path.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.core.config import Settings
from tests._support.extraction_fixtures import make_canned_result

__all__ = ["make_canned_result", "settings", "write_valid_skill"]


def write_valid_skill(base: Path) -> None:
    """Write a minimally valid `invoice@1` skill YAML under ``base``."""
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


def settings(skills_dir: Path, **overrides: object) -> Settings:
    """Return a `Settings` pinned to ``skills_dir`` with `app_env=development`.

    Explicit `app_env="development"` keeps `/openapi.json` served even
    when the ambient environment has `APP_ENV=production` set (which
    `create_app` uses to disable the OpenAPI route in prod).
    """
    return Settings(skills_dir=skills_dir, app_env="development", **overrides)  # type: ignore[reportCallIssue]

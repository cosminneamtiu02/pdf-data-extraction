"""Fixtures for skill YAML schema unit tests."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

SkillYamlFactory = Callable[..., Path]


class _Remove:
    """Sentinel for 'omit this key entirely' in the factory."""


REMOVE = _Remove()


@pytest.fixture
def write_skill_yaml(tmp_path: Path) -> SkillYamlFactory:
    """Return a factory that writes a skill YAML file and returns its path.

    Accepts a `filename` kwarg (default `1.yaml`) and arbitrary body overrides.
    Any body key set to the module-level `REMOVE` sentinel is excluded.
    """

    def _factory(*, filename: str = "1.yaml", **overrides: Any) -> Path:
        body: dict[str, Any] = {
            "name": "invoice",
            "version": 1,
            "description": "Invoice header extractor.",
            "prompt": "Extract invoice header fields.",
            "examples": [
                {"input": "INV-1 total 10", "output": {"number": "INV-1"}},
            ],
            "output_schema": {
                "type": "object",
                "properties": {"number": {"type": "string"}},
                "required": ["number"],
            },
        }
        for key, value in overrides.items():
            if value is REMOVE:
                body.pop(key, None)
            else:
                body[key] = value

        path = tmp_path / filename
        path.write_text(yaml.safe_dump(body), encoding="utf-8")
        return path

    return _factory

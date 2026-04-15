"""Unit tests for the `file` parameter on `SkillValidationFailedError` raises.

Added by PDFX-E002-F003. `SkillLoader` must thread the directory it was asked
to scan into every `SkillValidationFailedError`, so operators can tell which
`skills_dir` the failure came from even when the reason string is an
aggregated multi-file blob.
"""

from pathlib import Path

import pytest
import yaml

from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills.skill_loader import SkillLoader


def _write_valid(base: Path, *, dir_name: str, version: int) -> None:
    body = {
        "name": dir_name,
        "version": version,
        "prompt": "Extract header fields.",
        "examples": [{"input": "INV-1", "output": {"number": "INV-1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
    }
    target = base / dir_name
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{version}.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _params(exc: SkillValidationFailedError) -> dict[str, object]:
    assert exc.params is not None
    return exc.params.model_dump()


def test_missing_directory_raise_includes_file_param(tmp_path: Path) -> None:
    missing = tmp_path / "nope"

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(missing)

    dumped = _params(exc_info.value)
    assert dumped["file"] == str(missing)
    assert "nope" in str(dumped["reason"])


def test_single_bad_file_raise_includes_scan_root_as_file(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "1.yaml").write_text(": : not yaml :", encoding="utf-8")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    dumped = _params(exc_info.value)
    assert dumped["file"] == str(tmp_path)
    reason = str(dumped["reason"])
    assert "broken/1.yaml" in reason or "broken" in reason


def test_aggregated_failures_include_scan_root_as_file(tmp_path: Path) -> None:
    _write_valid(tmp_path, dir_name="invoice", version=1)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "1.yaml").write_text("--- bad yaml", encoding="utf-8")
    (tmp_path / "mismatch").mkdir()
    (tmp_path / "mismatch" / "2.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "mismatch",
                "version": 1,
                "prompt": "x",
                "examples": [{"input": "a", "output": {"number": "a"}}],
                "output_schema": {
                    "type": "object",
                    "properties": {"number": {"type": "string"}},
                    "required": ["number"],
                },
            },
        ),
        encoding="utf-8",
    )

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    dumped = _params(exc_info.value)
    assert dumped["file"] == str(tmp_path)
    reason = str(dumped["reason"])
    assert "broken" in reason
    assert "mismatch" in reason


def test_error_classes_importable_from_exceptions_package() -> None:
    from app.exceptions import SkillNotFoundError, SkillValidationFailedError
    from app.exceptions.base import DomainError

    assert issubclass(SkillNotFoundError, DomainError)
    assert issubclass(SkillValidationFailedError, DomainError)
    assert SkillNotFoundError.code == "SKILL_NOT_FOUND"
    assert SkillNotFoundError.http_status == 404
    assert SkillValidationFailedError.code == "SKILL_VALIDATION_FAILED"

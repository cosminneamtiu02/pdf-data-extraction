"""Unit tests for `SkillLoader` — filesystem walk + aggregated validation."""

import os
import stat
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills import SkillDoclingConfig
from app.features.extraction.skills import skill_loader as skill_loader_module
from app.features.extraction.skills.skill_loader import SkillLoader


def _reason(err: SkillValidationFailedError) -> str:
    assert err.params is not None
    dumped = err.params.model_dump()
    value = dumped["reason"]
    assert isinstance(value, str)
    return value


def _write_skill(base: Path, *, dir_name: str, **overrides: Any) -> Path:
    file_name = overrides.pop("file_name", "1.yaml")
    name = overrides.pop("name", dir_name)
    version = overrides.pop("version", 1)
    docling = overrides.pop("docling", None)
    body: dict[str, Any] = {
        "name": name or dir_name,
        "version": version,
        "description": overrides.pop("description", f"{dir_name} extractor."),
        "prompt": "Extract header fields.",
        "examples": [{"input": "INV-1", "output": {"number": "INV-1"}}],
        "output_schema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
    }
    if docling is not None:
        body["docling"] = docling

    target_dir = base / dir_name
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / file_name
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def test_load_three_valid_skills_returns_keyed_dict(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    _write_skill(tmp_path, dir_name="invoice", file_name="2.yaml", version=2)
    _write_skill(tmp_path, dir_name="research_paper", file_name="1.yaml", version=1)

    loaded = SkillLoader().load(tmp_path)

    assert set(loaded.keys()) == {
        ("invoice", 1),
        ("invoice", 2),
        ("research_paper", 1),
    }
    assert loaded[("invoice", 2)].version == 2
    assert loaded[("research_paper", 1)].name == "research_paper"


def test_load_empty_directory_returns_empty_dict_and_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Swap the loader module's `_logger` for a test double: structlog's global
    # state differs between local-only runs and the full CI session (where
    # `configure_logging()` has already been called by other fixtures), and
    # both `caplog` and `capture_logs()` depend on which regime is active.
    # A direct stub sidesteps that entire class of flake.
    events: list[tuple[str, dict[str, object]]] = []

    class _SpyLogger:
        def warning(self, event: str, **kwargs: object) -> None:
            events.append((event, kwargs))

    monkeypatch.setattr(skill_loader_module, "_logger", _SpyLogger())

    loaded = SkillLoader().load(tmp_path)

    assert loaded == {}
    assert any(event == "skill_manifest_empty" for event, _ in events)


def test_load_missing_directory_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope"

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(missing)

    assert "nope" in _reason(exc_info.value)


def test_load_top_level_yaml_raises_stray_file_error(tmp_path: Path) -> None:
    """A YAML at the skills_dir root violates the two-level layout. Previously
    the loader silently skipped it; now it fails loudly so misplaced skills
    can't disappear from the manifest without an operator signal.
    """
    stray = tmp_path / "stray.yaml"
    stray.write_text("name: stray\nversion: 1\n", encoding="utf-8")
    # Also include a valid skill so we verify aggregation across the two.
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    reason = _reason(exc_info.value)
    assert str(stray) in reason
    assert "stray YAML file" in reason
    # The error must include the actionable expected layout, with the actual
    # `skills_dir` path interpolated rather than a literal `<skills_dir>`.
    assert f"{tmp_path}/<name>/<version>.yaml" in reason
    assert "<skills_dir>" not in reason
    # And the relative-to-`skills_dir` path so operators can grep the offender.
    assert "'stray.yaml'" in reason


def test_load_three_level_nested_yaml_raises_stray_file_error(tmp_path: Path) -> None:
    (tmp_path / "invoice" / "archive").mkdir(parents=True)
    nested = tmp_path / "invoice" / "archive" / "1.yaml"
    nested.write_text("name: invoice\nversion: 1\n", encoding="utf-8")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    reason = _reason(exc_info.value)
    assert str(nested) in reason
    assert "stray YAML file" in reason
    assert "'invoice/archive/1.yaml'" in reason


def test_load_yml_extension_raises_stray_file_error(tmp_path: Path) -> None:
    (tmp_path / "invoice").mkdir()
    wrong_ext = tmp_path / "invoice" / "1.yml"
    wrong_ext.write_text("name: invoice\nversion: 1\n", encoding="utf-8")

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    reason = _reason(exc_info.value)
    assert str(wrong_ext) in reason
    # The `.yml` case is called out distinctly from a generic stray file so
    # operators see it's an extension typo, not a layout violation.
    assert "unsupported '.yml' extension" in reason
    assert "'invoice/1.yml'" in reason
    assert f"{tmp_path}/<name>/<version>.yaml" in reason


def test_load_filename_version_mismatch_raises(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="2.yaml", version=1)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    reason = _reason(exc_info.value)
    assert "2.yaml" in reason
    assert "filename version" in reason


def test_load_aggregates_multiple_broken_files(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "1.yaml").write_text(": : not yaml :", encoding="utf-8")
    _write_skill(tmp_path, dir_name="mismatch", file_name="2.yaml", version=1)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    reason = _reason(exc_info.value)
    assert "broken/1.yaml" in reason or "broken" in reason
    assert "mismatch/2.yaml" in reason or "mismatch" in reason


def test_load_directory_name_mismatch_raises(tmp_path: Path) -> None:
    """`invoice/1.yaml` with body `name: receipt` must not silently become `(receipt, 1)`."""
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", name="receipt", version=1)

    with pytest.raises(SkillValidationFailedError) as exc_info:
        SkillLoader().load(tmp_path)

    reason = _reason(exc_info.value)
    assert "directory name 'invoice'" in reason
    assert "body name 'receipt'" in reason


def test_load_applies_docling_defaults(tmp_path: Path) -> None:
    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)

    loader = SkillLoader(
        default_docling=SkillDoclingConfig(ocr="auto", table_mode="fast"),
    )
    loaded = loader.load(tmp_path)

    assert loaded[("invoice", 1)].docling_config.ocr == "auto"
    assert loaded[("invoice", 1)].docling_config.table_mode == "fast"


def test_load_skill_override_beats_default(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        dir_name="invoice",
        file_name="1.yaml",
        version=1,
        docling={"ocr": "off"},
    )

    loader = SkillLoader(
        default_docling=SkillDoclingConfig(ocr="auto", table_mode="fast"),
    )
    loaded = loader.load(tmp_path)

    skill = loaded[("invoice", 1)]
    assert skill.docling_config.ocr == "off"  # override wins
    assert skill.docling_config.table_mode == "fast"  # default fills gap


def test_load_unreadable_skill_file_raises_permission_error_not_validation_error(
    tmp_path: Path,
) -> None:
    """A filesystem permission failure must NOT masquerade as a skill validation
    error. Previously the loader's broad `except Exception` swallowed OSError /
    PermissionError from `SkillYamlSchema.load_from_file`'s `path.read_text`
    call into a `SkillValidationFailedError`, hiding the real (I/O) root cause
    behind a misleading "skill validation failed" label (issue #348).
    """
    if not hasattr(os, "geteuid"):
        pytest.skip("POSIX-only test: Windows lacks `os.geteuid` and `chmod(0o000)` semantics")
    if os.geteuid() == 0:
        pytest.skip("root bypasses 0o000 file permissions; test requires non-root uid")

    _write_skill(tmp_path, dir_name="invoice", file_name="1.yaml", version=1)
    path = tmp_path / "invoice" / "1.yaml"
    # `stat().st_mode` includes the file-type bits (e.g. `S_IFREG`); `chmod`
    # only wants permission bits, so mask via `stat.S_IMODE` to avoid relying
    # on platform-specific silent masking of the high bits.
    original_mode = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0o000)

    try:
        with pytest.raises(PermissionError):
            SkillLoader().load(tmp_path)
    finally:
        # Restore mode so tmp_path teardown can remove the file.
        path.chmod(original_mode)


@pytest.mark.slow
def test_load_100_skills_under_two_seconds(tmp_path: Path) -> None:
    for idx in range(100):
        _write_skill(
            tmp_path,
            dir_name=f"skill_{idx:03d}",
            file_name="1.yaml",
            name=f"skill_{idx:03d}",
            version=1,
        )

    start = time.perf_counter()
    loaded = SkillLoader().load(tmp_path)
    elapsed = time.perf_counter() - start

    assert len(loaded) == 100
    assert elapsed < 2.0

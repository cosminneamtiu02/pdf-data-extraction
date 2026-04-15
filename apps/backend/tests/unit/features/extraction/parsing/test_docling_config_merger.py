"""Unit tests for `merge_docling_config` (PDFX-E003-F003)."""

from typing import assert_type

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.docling_modes import OcrMode, TableMode
from app.features.extraction.parsing.docling_config import DoclingConfig
from app.features.extraction.parsing.docling_config_merger import merge_docling_config
from app.features.extraction.skills.skill_docling_config import SkillDoclingConfig


def _defaults() -> Settings:
    return Settings(docling_ocr_default="auto", docling_table_mode_default="fast")


def test_merge_with_no_override_returns_defaults() -> None:
    result = merge_docling_config(_defaults(), None)

    assert result == DoclingConfig(ocr="auto", table_mode="fast")


def test_merge_with_ocr_only_override_replaces_only_ocr() -> None:
    result = merge_docling_config(_defaults(), SkillDoclingConfig(ocr="force"))

    assert result == DoclingConfig(ocr="force", table_mode="fast")


def test_merge_with_table_mode_only_override_replaces_only_table_mode() -> None:
    result = merge_docling_config(
        _defaults(),
        SkillDoclingConfig(table_mode="accurate"),
    )

    assert result == DoclingConfig(ocr="auto", table_mode="accurate")


def test_merge_with_full_override_replaces_both_fields() -> None:
    result = merge_docling_config(
        _defaults(),
        SkillDoclingConfig(ocr="force", table_mode="accurate"),
    )

    assert result == DoclingConfig(ocr="force", table_mode="accurate")


def test_merge_is_pure_and_deterministic() -> None:
    settings = _defaults()
    override = SkillDoclingConfig(ocr="force")

    first = merge_docling_config(settings, override)
    second = merge_docling_config(settings, override)

    assert first == second
    assert settings.docling_ocr_default == "auto"
    assert settings.docling_table_mode_default == "fast"
    assert override.ocr == "force"
    assert override.table_mode is None


def test_settings_picks_up_docling_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCLING_OCR_DEFAULT", "force")
    monkeypatch.setenv("DOCLING_TABLE_MODE_DEFAULT", "accurate")

    # `_env_file=None` disables `.env` loading so a developer's local
    # `apps/backend/.env` cannot override the monkeypatched values and
    # make this test workstation-dependent.
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.docling_ocr_default == "force"
    assert settings.docling_table_mode_default == "accurate"
    assert merge_docling_config(settings, None) == DoclingConfig(
        ocr="force",
        table_mode="accurate",
    )


def test_skill_docling_config_rejects_unknown_ocr_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SkillDoclingConfig(ocr="banana")  # type: ignore[arg-type]

    errors = exc_info.value.errors()
    assert any(err["loc"] == ("ocr",) for err in errors)
    assert "banana" in str(exc_info.value)


def test_skill_docling_config_rejects_unknown_table_mode_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SkillDoclingConfig(table_mode="blazing")  # type: ignore[arg-type]

    errors = exc_info.value.errors()
    assert any(err["loc"] == ("table_mode",) for err in errors)


def test_skill_docling_config_forbids_unknown_keys() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SkillDoclingConfig(layout_mode="heavy")  # type: ignore[call-arg]

    assert any(err["type"] == "extra_forbidden" for err in exc_info.value.errors())


def test_merged_config_fields_are_literal_typed() -> None:
    """Pyright strict enforces that the merger narrows to Literal, not str.

    `assert_type` is a compile-time no-op at runtime but fails type-check if
    the declared type drifts away from the Literal aliases, satisfying the
    type-narrowing invariant from the feature spec's test scenarios.
    """
    result = merge_docling_config(_defaults(), None)

    assert_type(result.ocr, OcrMode)
    assert_type(result.table_mode, TableMode)
    assert result.ocr == "auto"
    assert result.table_mode == "fast"

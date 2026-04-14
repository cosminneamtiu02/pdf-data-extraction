"""Unit tests for DoclingConfig dataclass."""

import dataclasses

import pytest

from app.features.extraction.parsing.docling_config import DoclingConfig


def test_docling_config_constructs_with_fields() -> None:
    config = DoclingConfig(ocr="auto", table_mode="accurate")

    assert config.ocr == "auto"
    assert config.table_mode == "accurate"


def test_docling_config_is_frozen() -> None:
    config = DoclingConfig(ocr="auto", table_mode="accurate")

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.ocr = "off"  # type: ignore[misc]


@pytest.mark.parametrize("ocr", ["auto", "force", "off"])
def test_docling_config_accepts_all_valid_ocr_modes(ocr: str) -> None:
    config = DoclingConfig(ocr=ocr, table_mode="fast")

    assert config.ocr == ocr


@pytest.mark.parametrize("table_mode", ["fast", "accurate"])
def test_docling_config_accepts_all_valid_table_modes(table_mode: str) -> None:
    config = DoclingConfig(ocr="auto", table_mode=table_mode)

    assert config.table_mode == table_mode


@pytest.mark.parametrize("bad_ocr", ["froce", "FORCE", "", "none", "on"])
def test_docling_config_rejects_invalid_ocr_mode(bad_ocr: str) -> None:
    with pytest.raises(ValueError, match=r"DoclingConfig\.ocr must be one of"):
        DoclingConfig(ocr=bad_ocr, table_mode="fast")


@pytest.mark.parametrize("bad_table_mode", ["medium", "FAST", "", "precise"])
def test_docling_config_rejects_invalid_table_mode(bad_table_mode: str) -> None:
    with pytest.raises(ValueError, match=r"DoclingConfig\.table_mode must be one of"):
        DoclingConfig(ocr="auto", table_mode=bad_table_mode)

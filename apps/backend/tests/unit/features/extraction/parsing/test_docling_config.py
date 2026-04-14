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

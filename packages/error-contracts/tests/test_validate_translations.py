"""Tests for the translation validator."""

import json
from pathlib import Path

import pytest


REQUIRED_KEYS = {
    "version": 1,
    "namespace": "errors",
    "keys": ["NOT_FOUND", "WIDGET_NOT_FOUND"],
    "params_by_key": {
        "NOT_FOUND": [],
        "WIDGET_NOT_FOUND": ["widget_id"],
    },
}

COMPLETE_TRANSLATIONS = {
    "NOT_FOUND": "Resource not found.",
    "WIDGET_NOT_FOUND": "Widget '{{widget_id}}' was not found.",
}


@pytest.fixture
def required_keys_path(tmp_path: Path) -> Path:
    """Write required-keys.json and return its path."""
    path = tmp_path / "required-keys.json"
    path.write_text(json.dumps(REQUIRED_KEYS))
    return path


@pytest.fixture
def locales_dir(tmp_path: Path) -> Path:
    """Create a locales directory structure."""
    en_dir = tmp_path / "locales" / "en"
    en_dir.mkdir(parents=True)
    return tmp_path / "locales"


def _write_translation(locales_dir: Path, lang: str, data: dict) -> None:
    """Write a translation file."""
    lang_dir = locales_dir / lang
    lang_dir.mkdir(parents=True, exist_ok=True)
    (lang_dir / "errors.json").write_text(json.dumps(data))


def test_validator_passes_complete(required_keys_path: Path, locales_dir: Path):
    """Validator should pass when all keys and params are present."""
    _write_translation(locales_dir, "en", COMPLETE_TRANSLATIONS)

    from scripts.validate_translations import validate

    errors = validate(required_keys_path, locales_dir)
    assert errors == []


def test_validator_fails_missing_key(required_keys_path: Path, locales_dir: Path):
    """Validator should fail when a required key is missing."""
    incomplete = {"NOT_FOUND": "Resource not found."}
    _write_translation(locales_dir, "en", incomplete)

    from scripts.validate_translations import validate

    errors = validate(required_keys_path, locales_dir)
    assert len(errors) > 0
    assert any("WIDGET_NOT_FOUND" in e and "missing" in e.lower() for e in errors)


def test_validator_fails_extra_key(required_keys_path: Path, locales_dir: Path):
    """Validator should fail when an extra key exists."""
    extra = {**COMPLETE_TRANSLATIONS, "UNKNOWN_ERROR": "Something."}
    _write_translation(locales_dir, "en", extra)

    from scripts.validate_translations import validate

    errors = validate(required_keys_path, locales_dir)
    assert len(errors) > 0
    assert any("UNKNOWN_ERROR" in e and "extra" in e.lower() for e in errors)


def test_validator_fails_undefined_param(required_keys_path: Path, locales_dir: Path):
    """Validator should fail when a translation references a param not in the contract."""
    bad_param = {
        "NOT_FOUND": "Resource not found.",
        "WIDGET_NOT_FOUND": "Widget '{{widget_id}}' in {{region}} was not found.",
    }
    _write_translation(locales_dir, "en", bad_param)

    from scripts.validate_translations import validate

    errors = validate(required_keys_path, locales_dir)
    assert len(errors) > 0
    assert any("region" in e for e in errors)


def test_validator_fails_missing_param(required_keys_path: Path, locales_dir: Path):
    """Validator should fail when a translation omits a required param."""
    missing_param = {
        "NOT_FOUND": "Resource not found.",
        "WIDGET_NOT_FOUND": "Widget was not found.",  # Missing {{widget_id}}
    }
    _write_translation(locales_dir, "en", missing_param)

    from scripts.validate_translations import validate

    errors = validate(required_keys_path, locales_dir)
    assert len(errors) > 0
    assert any("widget_id" in e for e in errors)


def test_validator_fails_empty_translation(required_keys_path: Path, locales_dir: Path):
    """Validator should fail when a translation string is empty."""
    empty = {
        "NOT_FOUND": "",
        "WIDGET_NOT_FOUND": "Widget '{{widget_id}}' was not found.",
    }
    _write_translation(locales_dir, "en", empty)

    from scripts.validate_translations import validate

    errors = validate(required_keys_path, locales_dir)
    assert len(errors) > 0
    assert any("empty" in e.lower() for e in errors)

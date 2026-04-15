"""Pins the set of error codes declared in errors.yaml (PDFX-E001-F004).

Guards against accidental reintroduction of pruned CRUD-era codes (WIDGET_*,
CONFLICT, RATE_LIMITED) and against generated Python artifacts drifting from
the YAML source of truth.
"""

from pathlib import Path

import yaml

from app.exceptions._generated._registry import ERROR_CLASSES

EXPECTED_CODES = {
    "NOT_FOUND",
    "VALIDATION_FAILED",
    "INTERNAL_ERROR",
    "SKILL_VALIDATION_FAILED",
    "SKILL_NOT_FOUND",
    # PDF failure-mode errors (PDFX-E003-F004)
    "PDF_INVALID",
    "PDF_PASSWORD_PROTECTED",
    "PDF_TOO_MANY_PAGES",
    "PDF_NO_TEXT_EXTRACTABLE",
    "INTELLIGENCE_UNAVAILABLE",
    "STRUCTURED_OUTPUT_FAILED",
}

PRUNED_CODES = {
    "CONFLICT",
    "RATE_LIMITED",
    "WIDGET_NOT_FOUND",
    "WIDGET_NAME_CONFLICT",
    "WIDGET_NAME_TOO_LONG",
}

REPO_ROOT = Path(__file__).resolve().parents[5]
ERRORS_YAML = REPO_ROOT / "packages" / "error-contracts" / "errors.yaml"
GENERATED_DIR = REPO_ROOT / "apps" / "backend" / "app" / "exceptions" / "_generated"


def _yaml_codes() -> set[str]:
    data = yaml.safe_load(ERRORS_YAML.read_text())
    return set(data["errors"].keys())


def test_errors_yaml_contains_exactly_expected_codes() -> None:
    assert _yaml_codes() == EXPECTED_CODES


def test_errors_yaml_contains_no_pruned_codes() -> None:
    assert _yaml_codes().isdisjoint(PRUNED_CODES)


def test_generated_registry_matches_yaml() -> None:
    assert set(ERROR_CLASSES.keys()) == EXPECTED_CODES


def test_no_stale_generated_error_files() -> None:
    expected_stems = {
        "not_found_error",
        "validation_failed_error",
        "internal_error",
        "skill_validation_failed_error",
        "skill_not_found_error",
        "pdf_invalid_error",
        "pdf_password_protected_error",
        "pdf_too_many_pages_error",
        "pdf_no_text_extractable_error",
        "intelligence_unavailable_error",
        "structured_output_failed_error",
    }
    stale = {"conflict_error", "rate_limited_error", "widget_not_found_error"}
    present_stems = {p.stem for p in GENERATED_DIR.glob("*_error.py")}
    assert present_stems == expected_stems
    assert present_stems.isdisjoint(stale)

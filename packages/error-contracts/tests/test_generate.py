"""Tests for the error contracts code generator."""

import json
from pathlib import Path

import pytest


SAMPLE_YAML = """
version: 1
errors:
  NOT_FOUND:
    http_status: 404
    description: Resource not found
    params: {}
  WIDGET_NOT_FOUND:
    http_status: 404
    description: Widget not found
    params:
      widget_id: string
  WIDGET_NAME_TOO_LONG:
    http_status: 422
    description: Name too long
    params:
      name: string
      max_length: integer
      actual_length: integer
"""

DUPLICATE_YAML = """
version: 1
errors:
  NOT_FOUND:
    http_status: 404
    description: First
    params: {}
  NOT_FOUND:
    http_status: 404
    description: Duplicate
    params: {}
"""

INVALID_STATUS_YAML = """
version: 1
errors:
  BAD_ERROR:
    http_status: 200
    description: Not an error status
    params: {}
"""

INVALID_PARAM_TYPE_YAML = """
version: 1
errors:
  BAD_PARAMS:
    http_status: 400
    description: Bad param type
    params:
      items: list
"""


@pytest.fixture
def sample_errors_path(tmp_path: Path) -> Path:
    """Write sample errors.yaml and return its path."""
    path = tmp_path / "errors.yaml"
    path.write_text(SAMPLE_YAML)
    return path


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """Create and return a temporary output directory."""
    out = tmp_path / "output"
    out.mkdir()
    return out


def test_codegen_produces_valid_python(sample_errors_path: Path, output_dir: Path):
    """Codegen should produce one .py file per error class."""
    from scripts.generate import generate_python

    generate_python(sample_errors_path, output_dir)

    # Should produce: not_found_error.py, widget_not_found_error.py,
    # widget_not_found_params.py, widget_name_too_long_error.py,
    # widget_name_too_long_params.py, __init__.py, _registry.py
    assert (output_dir / "not_found_error.py").exists()
    assert (output_dir / "widget_not_found_error.py").exists()
    assert (output_dir / "widget_not_found_params.py").exists()
    assert (output_dir / "widget_name_too_long_error.py").exists()
    assert (output_dir / "widget_name_too_long_params.py").exists()
    assert (output_dir / "__init__.py").exists()
    assert (output_dir / "_registry.py").exists()

    # Verify content of a parameterized error
    content = (output_dir / "widget_not_found_error.py").read_text()
    assert "class WidgetNotFoundError" in content
    assert "WIDGET_NOT_FOUND" in content
    assert "404" in content


def test_codegen_produces_valid_typescript(sample_errors_path: Path, output_dir: Path):
    """Codegen should produce a valid TypeScript file with types."""
    from scripts.generate import generate_typescript

    ts_path = generate_typescript(sample_errors_path, output_dir / "generated.ts")

    content = ts_path.read_text()
    assert "ErrorCode" in content
    assert '"NOT_FOUND"' in content
    assert '"WIDGET_NOT_FOUND"' in content
    assert "widget_id: string" in content
    assert "ErrorParamsByCode" in content


def test_codegen_produces_valid_required_keys(
    sample_errors_path: Path, output_dir: Path
):
    """Codegen should produce a valid required-keys.json."""
    from scripts.generate import generate_required_keys

    json_path = generate_required_keys(
        sample_errors_path, output_dir / "required-keys.json"
    )

    data = json.loads(json_path.read_text())
    assert "keys" in data
    assert "NOT_FOUND" in data["keys"]
    assert "WIDGET_NOT_FOUND" in data["keys"]
    assert "params_by_key" in data
    assert data["params_by_key"]["WIDGET_NOT_FOUND"] == ["widget_id"]
    assert data["params_by_key"]["NOT_FOUND"] == []


def test_codegen_rejects_duplicate_codes(tmp_path: Path, output_dir: Path):
    """Codegen should reject YAML with duplicate error codes."""
    # YAML spec merges duplicate keys silently, so we detect via custom loader
    path = tmp_path / "errors.yaml"
    path.write_text(DUPLICATE_YAML)

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="[Dd]uplicate"):
        load_and_validate(path)


def test_codegen_rejects_invalid_http_status(tmp_path: Path, output_dir: Path):
    """Codegen should reject error codes with non-error HTTP status."""
    path = tmp_path / "errors.yaml"
    path.write_text(INVALID_STATUS_YAML)

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="[Ss]tatus"):
        load_and_validate(path)


def test_codegen_rejects_invalid_param_type(tmp_path: Path, output_dir: Path):
    """Codegen should reject params with unsupported types."""
    path = tmp_path / "errors.yaml"
    path.write_text(INVALID_PARAM_TYPE_YAML)

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="[Tt]ype"):
        load_and_validate(path)

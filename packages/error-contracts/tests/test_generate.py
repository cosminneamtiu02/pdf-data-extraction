"""Tests for the error contracts code generator."""

import ast
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


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   \n  \n",
        "- 1\n- 2\n",
        "just a string\n",
    ],
    ids=["empty", "whitespace", "list", "scalar"],
)
def test_codegen_rejects_non_mapping_yaml(tmp_path: Path, content: str):
    """Codegen must raise ValueError (not AttributeError) when the YAML
    top-level is not a mapping.

    yaml.safe_load returns None for empty/whitespace-only input, a list for
    sequences, and a str for bare scalars; calling .get on any of those would
    otherwise crash with AttributeError deep inside the generator.
    """
    path = tmp_path / "errors.yaml"
    path.write_text(content)

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="[Mm]apping"):
        load_and_validate(path)


def test_codegen_rejects_non_mapping_spec(tmp_path: Path):
    """Each error's spec must be a mapping; a scalar or list value must
    surface a clear ValueError instead of the `AttributeError` that bare
    `spec.get(...)` would raise.
    """
    path = tmp_path / "errors.yaml"
    path.write_text(
        'version: 1\nerrors:\n  FOO: "just-a-string-spec"\n',
    )

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="spec.*must be a mapping"):
        load_and_validate(path)


def test_codegen_rejects_non_mapping_params(tmp_path: Path):
    """`params` must be a mapping; a scalar or list must surface a clear
    ValueError instead of AttributeError from `.items()`.
    """
    path = tmp_path / "errors.yaml"
    path.write_text(
        "version: 1\n"
        "errors:\n"
        "  FOO:\n"
        "    http_status: 400\n"
        "    description: bad params shape\n"
        '    params: "not-a-dict"\n',
    )

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="params.*must be a mapping"):
        load_and_validate(path)


def test_codegen_rejects_non_string_error_code(tmp_path: Path):
    """YAML keys that parse as non-strings (e.g. integers, bools) must
    raise a clear ValueError before the regex match attempt.
    """
    path = tmp_path / "errors.yaml"
    # `42:` parses as an integer key in YAML; the rejection must come from
    # the type check, not a TypeError inside `re.match`.
    path.write_text(
        "version: 1\n"
        "errors:\n"
        "  42:\n"
        "    http_status: 400\n"
        "    description: integer key\n"
        "    params: {}\n",
    )

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="code.*must be a string"):
        load_and_validate(path)


def test_codegen_rejects_missing_errors_key(tmp_path: Path):
    """An `errors.yaml` without a top-level `errors:` key must surface a
    clear ValueError from `load_and_validate`, not a downstream KeyError
    when `generate_python`/`generate_typescript` index `data["errors"]`.
    """
    path = tmp_path / "errors.yaml"
    path.write_text("version: 1\n")

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="'errors' key"):
        load_and_validate(path)


@pytest.mark.parametrize(
    "param_name",
    ["max-length", "has space", "1starts_with_digit", "has.dot"],
    ids=["dash", "space", "leading-digit", "dot"],
)
def test_codegen_rejects_non_identifier_param_name(tmp_path: Path, param_name: str):
    """`param_name` must be a valid Python/TypeScript identifier so the
    generated `__init__` kwargs and TS interface fields compile. YAML keys
    like `max-length`, leading digits, or dots would pass shape validation
    but emit syntactically invalid code — the guard stops that at YAML
    load time with a clear ValueError pointing at the offending key.
    """
    path = tmp_path / "errors.yaml"
    path.write_text(
        "version: 1\n"
        "errors:\n"
        "  FOO:\n"
        "    http_status: 400\n"
        "    description: bad param identifier\n"
        f'    params: {{"{param_name}": "string"}}\n',
    )

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="param name"):
        load_and_validate(path)


def test_codegen_rejects_non_string_param_name(tmp_path: Path):
    """A non-string `param_name` (e.g., YAML integer key `1:`) must also
    be rejected with a clear ValueError, not a TypeError deeper in code
    generation.
    """
    path = tmp_path / "errors.yaml"
    path.write_text(
        "version: 1\n"
        "errors:\n"
        "  FOO:\n"
        "    http_status: 400\n"
        "    description: integer param key\n"
        "    params:\n"
        "      1: string\n",
    )

    from scripts.generate import load_and_validate

    with pytest.raises(ValueError, match="param name"):
        load_and_validate(path)


# Determinism regression test for issue #286 / PR #309.
#
# The bug: reordering top-level keys in `errors.yaml` produced byte-different
# generated outputs. CI's `task errors:check` gate diffs ALL THREE artifacts
# (Python `_registry.py`, TS `generated.ts`, JSON `required-keys.json`); any
# YAML-key-order change therefore drifted CI even though the semantic content
# was identical. The fix sorts entries by error code in every generator before
# emission, so the produced files are byte-identical regardless of input order.
_DETERMINISM_YAML_FORWARD = """
version: 1
errors:
  ALPHA_ERROR:
    http_status: 400
    description: First alphabetic
    params: {}
  BRAVO_ERROR:
    http_status: 404
    description: Second alphabetic
    params:
      thing_id: string
  CHARLIE_ERROR:
    http_status: 422
    description: Third alphabetic
    params:
      reason: string
      attempts: integer
"""

_DETERMINISM_YAML_REVERSED = """
version: 1
errors:
  CHARLIE_ERROR:
    http_status: 422
    description: Third alphabetic
    params:
      reason: string
      attempts: integer
  BRAVO_ERROR:
    http_status: 404
    description: Second alphabetic
    params:
      thing_id: string
  ALPHA_ERROR:
    http_status: 400
    description: First alphabetic
    params: {}
"""


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    """Helper: write a YAML string to a temp file and return its path."""
    path = tmp_path / name
    path.write_text(content)
    return path


def test_generate_python_is_deterministic_across_yaml_key_order(tmp_path: Path):
    """`generate_python` must emit byte-identical `_registry.py` and
    `__init__.py` regardless of YAML key order. Without this, reordering
    keys in `errors.yaml` causes false-positive drift in `task errors:check`.
    """
    from scripts.generate import generate_python

    out_forward = tmp_path / "out_forward"
    out_reversed = tmp_path / "out_reversed"
    out_forward.mkdir()
    out_reversed.mkdir()

    generate_python(
        _write_yaml(tmp_path, "forward.yaml", _DETERMINISM_YAML_FORWARD),
        out_forward,
    )
    generate_python(
        _write_yaml(tmp_path, "reversed.yaml", _DETERMINISM_YAML_REVERSED),
        out_reversed,
    )

    # Registry and __init__ are the load-bearing aggregate files; per-error
    # files have content keyed only by their own code so are trivially stable.
    assert (out_forward / "_registry.py").read_bytes() == (
        out_reversed / "_registry.py"
    ).read_bytes()
    assert (out_forward / "__init__.py").read_bytes() == (
        out_reversed / "__init__.py"
    ).read_bytes()


def test_generate_typescript_is_deterministic_across_yaml_key_order(tmp_path: Path):
    """`generate_typescript` must emit a byte-identical `generated.ts`
    regardless of YAML key order. The TS output participates in
    `task errors:check`, so non-determinism here also causes false drift.
    """
    from scripts.generate import generate_typescript

    ts_forward = generate_typescript(
        _write_yaml(tmp_path, "forward.yaml", _DETERMINISM_YAML_FORWARD),
        tmp_path / "forward.ts",
    )
    ts_reversed = generate_typescript(
        _write_yaml(tmp_path, "reversed.yaml", _DETERMINISM_YAML_REVERSED),
        tmp_path / "reversed.ts",
    )

    assert ts_forward.read_bytes() == ts_reversed.read_bytes()


def test_generate_required_keys_is_deterministic_across_yaml_key_order(
    tmp_path: Path,
):
    """`generate_required_keys` must emit a byte-identical
    `required-keys.json` regardless of YAML key order. The JSON output
    participates in `task errors:check`, so non-determinism here also causes
    false drift.
    """
    from scripts.generate import generate_required_keys

    json_forward = generate_required_keys(
        _write_yaml(tmp_path, "forward.yaml", _DETERMINISM_YAML_FORWARD),
        tmp_path / "forward.json",
    )
    json_reversed = generate_required_keys(
        _write_yaml(tmp_path, "reversed.yaml", _DETERMINISM_YAML_REVERSED),
        tmp_path / "reversed.json",
    )

    assert json_forward.read_bytes() == json_reversed.read_bytes()


# PR #309 review follow-up: also reorder param keys within an error spec.
# The earlier determinism fixtures only varied top-level error-code order.
# Param maps are set-like (translators treat them as unordered), so
# swapping `reason`/`attempts` must also keep every generated artifact
# byte-identical.
_PARAM_ORDER_YAML_FORWARD = """
version: 1
errors:
  CHARLIE_ERROR:
    http_status: 422
    description: Param-reorder case
    params:
      reason: string
      attempts: integer
"""

_PARAM_ORDER_YAML_SWAPPED = """
version: 1
errors:
  CHARLIE_ERROR:
    http_status: 422
    description: Param-reorder case
    params:
      attempts: integer
      reason: string
"""


def test_generate_python_is_deterministic_across_param_key_order(tmp_path: Path):
    """Swapping param keys within an error spec must not drift any Python
    artifact — registry, params class, error class, or `__init__`.
    """
    from scripts.generate import generate_python

    out_forward = tmp_path / "out_forward"
    out_swapped = tmp_path / "out_swapped"
    out_forward.mkdir()
    out_swapped.mkdir()

    generate_python(
        _write_yaml(tmp_path, "forward.yaml", _PARAM_ORDER_YAML_FORWARD),
        out_forward,
    )
    generate_python(
        _write_yaml(tmp_path, "swapped.yaml", _PARAM_ORDER_YAML_SWAPPED),
        out_swapped,
    )

    for py_file in (
        "_registry.py",
        "__init__.py",
        "charlie_error.py",
        "charlie_params.py",
    ):
        assert (out_forward / py_file).read_bytes() == (
            out_swapped / py_file
        ).read_bytes(), f"{py_file} drifted when params keys were reordered"


def test_generate_typescript_is_deterministic_across_param_key_order(tmp_path: Path):
    """Swapping param keys within an error spec must not drift the TS output."""
    from scripts.generate import generate_typescript

    ts_forward = generate_typescript(
        _write_yaml(tmp_path, "forward.yaml", _PARAM_ORDER_YAML_FORWARD),
        tmp_path / "forward.ts",
    )
    ts_swapped = generate_typescript(
        _write_yaml(tmp_path, "swapped.yaml", _PARAM_ORDER_YAML_SWAPPED),
        tmp_path / "swapped.ts",
    )

    assert ts_forward.read_bytes() == ts_swapped.read_bytes()


def test_generate_required_keys_is_deterministic_across_param_key_order(
    tmp_path: Path,
):
    """Swapping param keys within an error spec must not drift
    `required-keys.json`. `params_by_key` is translator-facing metadata
    (order-insensitive by semantics), so sorted output keeps the artifact
    byte-stable under the semantically-equivalent YAML reorder.
    """
    from scripts.generate import generate_required_keys

    json_forward = generate_required_keys(
        _write_yaml(tmp_path, "forward.yaml", _PARAM_ORDER_YAML_FORWARD),
        tmp_path / "forward.json",
    )
    json_swapped = generate_required_keys(
        _write_yaml(tmp_path, "swapped.yaml", _PARAM_ORDER_YAML_SWAPPED),
        tmp_path / "swapped.json",
    )

    assert json_forward.read_bytes() == json_swapped.read_bytes()


# Provenance-in-docstring regression test for issue #373.
#
# The TypeScript generator output begins with:
#   `// THIS FILE IS GENERATED FROM errors.yaml`
#   `// DO NOT EDIT BY HAND. Run `task errors:generate` to regenerate.`
# while the Python module docstrings only said
#   `"""Generated from errors.yaml. Do not edit."""`
# Contributors reading a generated Python file had no in-file pointer to
# the regenerate command, forcing them to hunt for it. These tests pin
# the regenerate-command mention in every Python artifact type the
# generator emits (per-error class, params class, __init__.py, _registry.py)
# so the provenance cannot silently regress.
_PROVENANCE_YAML = """
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
"""


def test_generated_python_files_mention_regenerate_command(
    tmp_path: Path, output_dir: Path
):
    """Every generated Python artifact's module docstring must reference
    ``task errors:generate`` so a contributor reading the file sees the
    regeneration command without having to check the TypeScript output or
    the root Taskfile (issue #373).

    The check is pinned against the module docstring specifically (the
    first statement in the file, via ``ast.get_docstring``) rather than
    a whole-file substring search. A whole-file check would silently pass
    if the hint regressed out of the docstring but appeared later in a
    class docstring, comment, or string literal (PR #517 review).
    """
    from scripts.generate import generate_python

    errors_path = tmp_path / "errors.yaml"
    errors_path.write_text(_PROVENANCE_YAML)

    generate_python(errors_path, output_dir)

    # Every emitted file type: per-error class, params class, aggregate
    # __init__.py, and _registry.py.
    for py_file in (
        "not_found_error.py",
        "widget_not_found_error.py",
        "widget_not_found_params.py",
        "__init__.py",
        "_registry.py",
    ):
        content = (output_dir / py_file).read_text()
        module_docstring = ast.get_docstring(ast.parse(content))
        assert module_docstring is not None, (
            f"{py_file} must have a module docstring as its first statement "
            f"(issue #373). Got file content:\n{content[:300]}"
        )
        assert "task errors:generate" in module_docstring, (
            f"{py_file} module docstring must mention `task errors:generate` "
            f"so contributors find the regeneration command in-file "
            f"(issue #373). Got docstring:\n{module_docstring}"
        )

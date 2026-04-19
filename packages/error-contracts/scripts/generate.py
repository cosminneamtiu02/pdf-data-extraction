"""Code generator: errors.yaml -> Python exception classes + TypeScript types + required-keys.json.

Each error code produces one Python file per class (error + params if any).
Generated files are committed but never edited by hand.
"""

import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

from scripts._duplicate_key_safe_loader import DuplicateKeyDetectingSafeLoader

# The YAML shape is: {"version": int, "errors": {CODE: {http_status: int, description: str, params: {name: type}}}}.
# We keep the in-memory representation duck-typed as dict[str, Any] because
# yaml.safe_load returns Any; validation happens in `load_and_validate`.
ErrorSpec = dict[str, Any]
ErrorsYaml = dict[str, Any]

VALID_PARAM_TYPES = {"string", "integer", "number", "boolean"}
PARAM_TYPE_TO_PYTHON: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
}
PARAM_TYPE_TO_TS: dict[str, str] = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
}
# Matches the `line-length = 100` setting in apps/backend/pyproject.toml so
# generated imports are pre-formatted consistently with ruff format --check.
_PY_LINE_LENGTH_LIMIT = 100


def _py_import_line(module: str, name: str) -> str:
    """Format a `from <module> import <name>` line, wrapping if > line-length.

    Ruff format will wrap a long single-name import into a parenthesised form;
    we pre-emit the wrapped shape when the flat form would exceed the project
    line-length so the generator's output is idempotent with ruff format.
    """
    flat = f"from {module} import {name}"
    if len(flat) <= _PY_LINE_LENGTH_LIMIT:
        return flat
    return f"from {module} import (\n    {name},\n)"


def _code_to_class_name(code: str) -> str:
    """Convert SCREAMING_SNAKE to PascalCase error class name.

    Appends 'Error' unless the name already ends with 'Error'.
    e.g. WIDGET_NOT_FOUND -> WidgetNotFoundError
         INTERNAL_ERROR -> InternalError (not InternalErrorError)
    """
    base = "".join(word.capitalize() for word in code.lower().split("_"))
    if base.endswith("Error"):
        return base
    return base + "Error"


def _class_to_snake(name: str) -> str:
    """Convert PascalCase to snake_case. e.g. WidgetNotFoundError -> widget_not_found_error."""
    s = re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")
    return s


def load_and_validate(errors_path: Path) -> ErrorsYaml:
    """Load errors.yaml and validate its contents.

    Uses ``DuplicateKeyDetectingSafeLoader`` (issue #294): the prior
    regex-based duplicate-key check missed keys written with leading
    tabs, trailing whitespace before the colon, quoted form, flow-style
    mappings, or any mapping nested deeper than the baked-in two-space
    indent. The loader subclass overrides mapping construction so every
    duplicate fires a ``ConstructorError`` at parse time.

    Every ``yaml.YAMLError`` (the superclass of ``ConstructorError``,
    ``ParserError``, ``ScannerError``, …) is re-raised as ``ValueError``
    with the source path prepended so callers keep a single
    ``except ValueError`` handler regardless of which sub-failure YAML
    raised. Duplicate-key detection is only one of several YAML-layer
    failures that now surface through the same contract.
    """
    raw_text = errors_path.read_text()

    try:
        loaded = yaml.load(  # noqa: S506 — DuplicateKeyDetectingSafeLoader is a SafeLoader subclass
            raw_text,
            Loader=DuplicateKeyDetectingSafeLoader,
        )
    except yaml.YAMLError as exc:
        msg = f"YAML error in {errors_path}: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(loaded, dict):
        msg = f"errors.yaml top-level must be a mapping, got {type(loaded).__name__}"
        raise ValueError(msg)
    data = cast("ErrorsYaml", loaded)

    if "errors" not in data:
        msg = "errors.yaml missing required 'errors' key at the top level"
        raise ValueError(msg)
    errors_raw = data["errors"]
    if not isinstance(errors_raw, dict):
        msg = (
            f"errors.yaml 'errors' key must be a mapping, got "
            f"{type(errors_raw).__name__}"
        )
        raise ValueError(msg)
    errors = cast("dict[str, ErrorSpec]", errors_raw)

    for code, spec in errors.items():
        # YAML parses bare int/bool/null keys as non-strings, and scalar
        # or list values as non-dicts. The surrounding `cast` suppresses
        # the types pyright needs to see, but the runtime shape is still
        # reachable through malformed YAML — these guards are load-bearing
        # at runtime even though pyright considers them redundant under
        # strict mode.
        if not isinstance(code, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = f"Error code must be a string, got {type(code).__name__}: {code!r}"
            raise ValueError(msg)
        if not re.match(r"^[A-Z][A-Z0-9_]*$", code):
            msg = f"Error code must be SCREAMING_SNAKE_CASE: {code}"
            raise ValueError(msg)

        if not isinstance(spec, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = f"Error spec for {code} must be a mapping, got {type(spec).__name__}"
            raise ValueError(msg)

        # Validate http_status
        status = spec.get("http_status")
        if not isinstance(status, int) or status < 400 or status > 599:
            msg = f"Invalid HTTP status {status} for {code}. Must be 400-599."
            raise ValueError(msg)

        # Validate params shape + contents
        params_raw = spec.get("params", {})
        if not isinstance(params_raw, dict):
            msg = (
                f"'params' for {code} must be a mapping, got "
                f"{type(params_raw).__name__}"
            )
            raise ValueError(msg)
        params = cast("dict[str, str]", params_raw)
        for param_name, param_type in params.items():
            # Param names become kwargs on generated __init__ signatures and
            # field names on TS interfaces. YAML keys that aren't valid
            # identifiers ("max-length", "1x", "a.b") would emit code that
            # fails to parse; reject them at load time with a clear error.
            if not isinstance(param_name, str):  # pyright: ignore[reportUnnecessaryIsInstance]
                msg = (
                    f"param name for {code} must be a string, got "
                    f"{type(param_name).__name__}: {param_name!r}"
                )
                raise ValueError(msg)
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", param_name):
                msg = (
                    f"Invalid param name {param_name!r} for {code}: "
                    f"must be a valid identifier (^[a-zA-Z_][a-zA-Z0-9_]*$)"
                )
                raise ValueError(msg)
            if param_type not in VALID_PARAM_TYPES:
                msg = (
                    f"Invalid param type '{param_type}' for {code}.{param_name}. "
                    f"Must be one of: {', '.join(sorted(VALID_PARAM_TYPES))}"
                )
                raise ValueError(msg)

    return data


def generate_python(errors_path: Path, output_dir: Path) -> list[Path]:
    """Generate Python exception classes from errors.yaml."""
    data = load_and_validate(errors_path)
    errors = cast("dict[str, ErrorSpec]", data["errors"])
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_files: list[Path] = []

    # Each entry is (module_path, exported_name); the final __init__ and
    # _registry emitters derive both the `from X import Y` line (wrapped when
    # it would exceed the project line-length) and the `__all__` / filter key
    # from this tuple — we can't re-parse a wrapped import line to recover
    # the exported name.
    init_imports: list[tuple[str, str]] = []
    registry_entries: list[str] = []

    for code, spec in errors.items():
        error_class_name = _code_to_class_name(code)
        base_name = error_class_name.removesuffix("Error")
        error_file_stem = _class_to_snake(error_class_name)  # e.g. "internal_error"
        params = cast("dict[str, str]", spec.get("params", {}))
        http_status = cast("int", spec["http_status"])

        # Generate error class (and params class first, when params exist, so
        # the two branches share the scope of params_class_name / params_file_stem).
        error_file = output_dir / f"{error_file_stem}.py"
        if params:
            params_class_name = f"{base_name}Params"
            params_file_stem = _class_to_snake(params_class_name)
            params_file = output_dir / f"{params_file_stem}.py"
            fields = "\n".join(
                f"    {name}: {PARAM_TYPE_TO_PYTHON[ptype]}"
                for name, ptype in params.items()
            )
            params_file.write_text(
                f'"""Generated from errors.yaml. Do not edit."""\n\n'
                f"from pydantic import BaseModel\n\n\n"
                f"class {params_class_name}(BaseModel):\n"
                f'    """Parameters for {code} error."""\n\n'
                f"{fields}\n"
            )
            generated_files.append(params_file)
            init_imports.append(
                (f"app.exceptions._generated.{params_file_stem}", params_class_name)
            )

            kw_args = [
                f"{name}: {PARAM_TYPE_TO_PYTHON[ptype]}"
                for name, ptype in params.items()
            ]
            init_signature = ", ".join(kw_args)
            params_construct = ", ".join(f"{name}={name}" for name in params)
            # Check if the super().__init__ line would exceed the project
            # line-length (matches ruff `line-length = 100` via _PY_LINE_LENGTH_LIMIT).
            super_line = f"        super().__init__(params={params_class_name}({params_construct}))"
            if len(super_line) > _PY_LINE_LENGTH_LIMIT:
                params_lines = ",\n                ".join(
                    f"{name}={name}" for name in params
                )
                super_block = (
                    f"        super().__init__(\n"
                    f"            params={params_class_name}(\n"
                    f"                {params_lines},\n"
                    f"            ),\n"
                    f"        )\n"
                )
            else:
                super_block = super_line + "\n"
            params_import_line = _py_import_line(
                f"app.exceptions._generated.{params_file_stem}", params_class_name
            )
            error_content = (
                f'"""Generated from errors.yaml. Do not edit."""\n\n'
                f"from typing import ClassVar\n\n"
                f"{params_import_line}\n"
                f"from app.exceptions.base import DomainError\n\n\n"
                f"class {error_class_name}(DomainError):\n"
                f'    """Error: {code}."""\n\n'
                f'    code: ClassVar[str] = "{code}"\n'
                f"    http_status: ClassVar[int] = {http_status}\n\n"
                f"    def __init__(self, *, {init_signature}) -> None:\n" + super_block
            )
        else:
            error_content = (
                f'"""Generated from errors.yaml. Do not edit."""\n\n'
                f"from typing import ClassVar\n\n"
                f"from app.exceptions.base import DomainError\n\n\n"
                f"class {error_class_name}(DomainError):\n"
                f'    """Error: {code}."""\n\n'
                f'    code: ClassVar[str] = "{code}"\n'
                f"    http_status: ClassVar[int] = {http_status}\n\n"
                f"    def __init__(self) -> None:\n"
                f"        super().__init__(params=None)\n"
            )

        error_file.write_text(error_content)
        generated_files.append(error_file)
        init_imports.append(
            (f"app.exceptions._generated.{error_file_stem}", error_class_name)
        )
        registry_entries.append(f'    "{code}": {error_class_name},')

    # Generate __init__.py (sorted imports for deterministic output). Sort by
    # the exported name so the output order is stable regardless of module
    # path length.
    sorted_imports = sorted(init_imports, key=lambda entry: entry[1])
    init_file = output_dir / "__init__.py"
    init_content = (
        '"""Generated error classes. Do not edit."""\n\n'
        + "\n".join(_py_import_line(module, name) for module, name in sorted_imports)
        + "\n\n__all__ = [\n"
        + "\n".join(f'    "{name}",' for _module, name in sorted_imports)
        + "\n]\n"
    )
    init_file.write_text(init_content)
    generated_files.append(init_file)

    # Generate _registry.py (sorted imports for deterministic output). Filter
    # to error classes only (not Params classes) by exported-name suffix.
    registry_file = output_dir / "_registry.py"
    error_imports = sorted(
        (module, name)
        for module, name in init_imports
        if "Error" in name and "Params" not in name
    )
    registry_content = (
        '"""Generated error registry. Do not edit."""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from app.exceptions.base import DomainError\n\n"
        + "\n".join(_py_import_line(module, name) for module, name in error_imports)
        + "\n\n"
        + "ERROR_CLASSES: dict[str, type[DomainError]] = {\n"
        + "\n".join(registry_entries)
        + "\n}\n"
    )
    registry_file.write_text(registry_content)
    generated_files.append(registry_file)

    return generated_files


def generate_typescript(errors_path: Path, output_path: Path) -> Path:
    """Generate TypeScript types from errors.yaml."""
    data = load_and_validate(errors_path)
    errors = cast("dict[str, ErrorSpec]", data["errors"])

    codes_array = ", ".join(f'"{code}"' for code in errors)

    params_entries: list[str] = []
    status_entries: list[str] = []
    for code, spec in errors.items():
        params = cast("dict[str, str]", spec.get("params", {}))
        if params:
            fields = "; ".join(
                f"{name}: {PARAM_TYPE_TO_TS[ptype]}" for name, ptype in params.items()
            )
            params_entries.append(f"  {code}: {{ {fields} }};")
        else:
            params_entries.append(f"  {code}: Record<string, never>;")
        status_entries.append(f"  {code}: {spec['http_status']},")

    content = (
        "// THIS FILE IS GENERATED FROM errors.yaml\n"
        "// DO NOT EDIT BY HAND. Run `task errors:generate` to regenerate.\n\n"
        f"export type ErrorCode =\n  | {'\n  | '.join(f'"{code}"' for code in errors)};\n\n"
        "export interface ErrorParamsByCode {\n" + "\n".join(params_entries) + "\n}\n\n"
        "export interface ApiErrorPayload<C extends ErrorCode = ErrorCode> {\n"
        "  code: C;\n"
        "  params: ErrorParamsByCode[C];\n"
        "  details: Array<{ field: string; reason: string }> | null;\n"
        "  request_id: string;\n"
        "}\n\n"
        f"export const ERROR_CODES: readonly ErrorCode[] = [{codes_array}] as const;\n\n"
        "export const HTTP_STATUS_BY_CODE: Record<ErrorCode, number> = {\n"
        + "\n".join(status_entries)
        + "\n};\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)
    return output_path


def generate_required_keys(errors_path: Path, output_path: Path) -> Path:
    """Generate required-keys.json for translation validation."""
    data = load_and_validate(errors_path)
    errors = cast("dict[str, ErrorSpec]", data["errors"])

    keys = list(errors.keys())
    params_by_key: dict[str, list[str]] = {
        code: list(cast("dict[str, str]", spec.get("params", {})).keys())
        for code, spec in errors.items()
    }

    result = {
        "version": 1,
        "namespace": "errors",
        "keys": keys,
        "params_by_key": params_by_key,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return output_path

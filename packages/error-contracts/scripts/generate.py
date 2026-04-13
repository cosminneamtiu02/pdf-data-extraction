"""Code generator: errors.yaml -> Python exception classes + TypeScript types + required-keys.json.

Each error code produces one Python file per class (error + params if any).
Generated files are committed but never edited by hand.
"""

import json
import re
from pathlib import Path

import yaml

VALID_PARAM_TYPES = {"string", "integer", "number", "boolean"}
PARAM_TYPE_TO_PYTHON = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
}
PARAM_TYPE_TO_TS = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
}


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


def _code_to_snake(code: str) -> str:
    """Convert SCREAMING_SNAKE to snake_case. e.g. WIDGET_NOT_FOUND -> widget_not_found."""
    return code.lower()


def _class_to_snake(name: str) -> str:
    """Convert PascalCase to snake_case. e.g. WidgetNotFoundError -> widget_not_found_error."""
    s = re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")
    return s


def _detect_duplicate_keys(raw_text: str) -> None:
    """Detect duplicate top-level error code keys in the YAML text."""
    lines = raw_text.split("\n")
    in_errors = False
    seen_codes: set[str] = set()
    indent_pattern = re.compile(r"^  (\w+):$")

    for line in lines:
        stripped = line.rstrip()
        if stripped == "errors:":
            in_errors = True
            continue
        if in_errors:
            m = indent_pattern.match(stripped)
            if m:
                code = m.group(1)
                if code in seen_codes:
                    msg = f"Duplicate error code: {code}"
                    raise ValueError(msg)
                seen_codes.add(code)
            elif (
                stripped
                and not stripped.startswith(" ")
                and not stripped.startswith("#")
            ):
                in_errors = False


def load_and_validate(errors_path: Path) -> dict:
    """Load errors.yaml and validate its contents."""
    raw_text = errors_path.read_text()
    _detect_duplicate_keys(raw_text)

    data = yaml.safe_load(raw_text)
    errors = data.get("errors", {})

    for code, spec in errors.items():
        # Validate code format
        if not re.match(r"^[A-Z][A-Z0-9_]*$", code):
            msg = f"Error code must be SCREAMING_SNAKE_CASE: {code}"
            raise ValueError(msg)

        # Validate http_status
        status = spec.get("http_status")
        if not isinstance(status, int) or status < 400 or status > 599:
            msg = f"Invalid HTTP status {status} for {code}. Must be 400-599."
            raise ValueError(msg)

        # Validate param types
        params = spec.get("params", {})
        for param_name, param_type in params.items():
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
    errors = data["errors"]
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_files: list[Path] = []

    init_imports: list[str] = []
    registry_entries: list[str] = []

    for code, spec in errors.items():
        error_class_name = _code_to_class_name(code)
        base_name = error_class_name.removesuffix("Error")
        error_file_stem = _class_to_snake(error_class_name)  # e.g. "internal_error"
        params = spec.get("params", {})
        http_status = spec["http_status"]

        # Generate params class if params exist
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
                f"from app.exceptions._generated.{params_file_stem} import {params_class_name}"
            )

        # Generate error class
        error_file = output_dir / f"{error_file_stem}.py"
        if params:
            kw_args = []
            for name, ptype in params.items():
                kw_args.append(f"{name}: {PARAM_TYPE_TO_PYTHON[ptype]}")
            init_signature = ", ".join(kw_args)
            params_construct = ", ".join(f"{name}={name}" for name in params)
            # Check if the super().__init__ line would exceed 100 chars (ruff line-length)
            super_line = f"        super().__init__(params={params_class_name}({params_construct}))"
            if len(super_line) > 100:
                params_lines = ",\n                ".join(f"{name}={name}" for name in params)
                super_block = (
                    f"        super().__init__(\n"
                    f"            params={params_class_name}(\n"
                    f"                {params_lines},\n"
                    f"            ),\n"
                    f"        )\n"
                )
            else:
                super_block = super_line + "\n"
            error_content = (
                f'"""Generated from errors.yaml. Do not edit."""\n\n'
                f"from typing import ClassVar\n\n"
                f"from app.exceptions._generated.{params_file_stem} import {params_class_name}\n"
                f"from app.exceptions.base import DomainError\n\n\n"
                f"class {error_class_name}(DomainError):\n"
                f'    """Error: {code}."""\n\n'
                f'    code: ClassVar[str] = "{code}"\n'
                f"    http_status: ClassVar[int] = {http_status}\n\n"
                f"    def __init__(self, *, {init_signature}) -> None:\n"
                + super_block
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
            f"from app.exceptions._generated.{error_file_stem} import {error_class_name}"
        )
        registry_entries.append(f'    "{code}": {error_class_name},')

    # Generate __init__.py (sorted imports for deterministic output)
    sorted_imports = sorted(init_imports)
    init_file = output_dir / "__init__.py"
    init_content = (
        '"""Generated error classes. Do not edit."""\n\n'
        + "\n".join(sorted_imports)
        + "\n\n__all__ = [\n"
        + "\n".join(f'    "{imp.split()[-1]}",' for imp in sorted_imports)
        + "\n]\n"
    )
    init_file.write_text(init_content)
    generated_files.append(init_file)

    # Generate _registry.py (sorted imports for deterministic output)
    registry_file = output_dir / "_registry.py"
    error_imports = sorted(
        imp
        for imp in init_imports
        if "Error" in imp.split()[-1] and "Params" not in imp.split()[-1]
    )
    registry_content = (
        '"""Generated error registry. Do not edit."""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from app.exceptions.base import DomainError\n\n"
        + "\n".join(error_imports)
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
    errors = data["errors"]

    codes_array = ", ".join(f'"{code}"' for code in errors)

    params_entries: list[str] = []
    status_entries: list[str] = []
    for code, spec in errors.items():
        params = spec.get("params", {})
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
    errors = data["errors"]

    keys = list(errors.keys())
    params_by_key = {
        code: list(spec.get("params", {}).keys()) for code, spec in errors.items()
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

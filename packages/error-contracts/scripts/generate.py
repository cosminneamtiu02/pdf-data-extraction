"""Code generator: errors.yaml -> Python exception classes + TypeScript types + required-keys.json.

Each error code produces one Python file per class (error + params if any).
Generated files are committed but never edited by hand.

Also exposes a ``__main__`` entry point (issue #372) so the module can be
invoked as ``python -m scripts.generate`` when ``packages/error-contracts``
is on ``sys.path`` (for example, by running from that directory — as
``task errors:generate`` and the CI workflow both do — or by setting
``PYTHONPATH``), with no reliance on a fragile inline ``python -c '...'``
string. ``task errors:generate`` and the local developer loop now call
``python -m scripts.generate`` directly. The ``scripts.generate_all``
wrapper from issue #365 remains the CI entry point
(``.github/workflows/ci.yml`` "Regenerate error contracts" step); drift
between the two entry points is mechanically prevented because
``scripts.generate_all.main`` now delegates to ``main()`` here rather
than duplicating the default-path constants. ``task errors:check`` (plus
CI's "Verify generated files are up to date" diff) still catches any
drift in the generated artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

import yaml

from scripts._duplicate_key_safe_loader import DuplicateKeyDetectingSafeLoader

# The YAML shape is: {"version": int, "errors": {CODE: {http_status: int, description: str, params: {name: type}}}}.
# We keep the in-memory representation duck-typed as dict[str, Any] because
# yaml.load with our DuplicateKeyDetectingSafeLoader subclass (a SafeLoader
# that rejects duplicate mapping keys at parse time) returns Any; validation
# happens in `load_and_validate`.
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
        # Iterate params by sorted key name so reordering param keys in
        # errors.yaml (a semantic no-op — params are set-like) produces
        # byte-stable Python artifacts emitted by this function: the
        # generated Params class field order and the error class's
        # __init__ signature plus the Params(...) constructor call.
        # TS interface field order and required-keys.json param ordering
        # are independently stabilised by the `sorted(params.items())` /
        # `sorted(...keys())` calls inside `generate_typescript` and
        # `generate_required_keys` respectively — see those functions if
        # touching that determinism. (issue #286 extended per PR #309 review.)
        sorted_param_items = sorted(params.items())
        sorted_param_names = [name for name, _ in sorted_param_items]
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
                for name, ptype in sorted_param_items
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
                for name, ptype in sorted_param_items
            ]
            init_signature = ", ".join(kw_args)
            params_construct = ", ".join(
                f"{name}={name}" for name in sorted_param_names
            )
            # Check if the super().__init__ line would exceed the project
            # line-length (matches ruff `line-length = 100` via _PY_LINE_LENGTH_LIMIT).
            super_line = f"        super().__init__(params={params_class_name}({params_construct}))"
            if len(super_line) > _PY_LINE_LENGTH_LIMIT:
                params_lines = ",\n                ".join(
                    f"{name}={name}" for name in sorted_param_names
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
    # Sort registry_entries too (imports are already sorted above) so the
    # generated dict body is deterministic regardless of YAML key order.
    # Without this sort, two developers reordering YAML keys produced
    # diverging _registry.py diffs that CI's "regenerated content matches"
    # check then flagged as drift (issue #286). Each entry starts with four
    # spaces + `"<CODE>":`, so string sort gives alphabetical order by error
    # code in the generated dict body. Note: this is a different sort key
    # than `error_imports` above, which sorts by `(module, name)` tuple
    # (default tuple ordering); both are alphabetical, but on different
    # fields. The two sorts are deliberately independent — the imports'
    # order only affects `from ... import ...` line layout, while this sort
    # affects the user-visible dict-key order.
    sorted_registry_entries = sorted(registry_entries)
    registry_content = (
        '"""Generated error registry. Do not edit."""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from app.exceptions.base import DomainError\n\n"
        + "\n".join(_py_import_line(module, name) for module, name in error_imports)
        + "\n\n"
        + "ERROR_CLASSES: dict[str, type[DomainError]] = {\n"
        + "\n".join(sorted_registry_entries)
        + "\n}\n"
    )
    registry_file.write_text(registry_content)
    generated_files.append(registry_file)

    return generated_files


def generate_typescript(errors_path: Path, output_path: Path) -> Path:
    """Generate TypeScript types from errors.yaml."""
    data = load_and_validate(errors_path)
    errors = cast("dict[str, ErrorSpec]", data["errors"])

    # Sort by error code for deterministic output regardless of YAML key
    # order. Drives the ErrorCode union, ErrorParamsByCode interface,
    # ERROR_CODES tuple, and HTTP_STATUS_BY_CODE map — all four would
    # otherwise drift in `task errors:check` whenever YAML keys are
    # reordered (issue #286).
    sorted_codes = sorted(errors.keys())

    codes_array = ", ".join(f'"{code}"' for code in sorted_codes)

    params_entries: list[str] = []
    status_entries: list[str] = []
    for code in sorted_codes:
        spec = errors[code]
        params = cast("dict[str, str]", spec.get("params", {}))
        if params:
            # Sort param names so reordering param keys in errors.yaml
            # produces byte-stable TS output (PR #309 review: nested
            # mappings also need deterministic iteration, not just the
            # top-level error-code keys).
            fields = "; ".join(
                f"{name}: {PARAM_TYPE_TO_TS[ptype]}"
                for name, ptype in sorted(params.items())
            )
            params_entries.append(f"  {code}: {{ {fields} }};")
        else:
            params_entries.append(f"  {code}: Record<string, never>;")
        status_entries.append(f"  {code}: {spec['http_status']},")

    content = (
        "// THIS FILE IS GENERATED FROM errors.yaml\n"
        "// DO NOT EDIT BY HAND. Run `task errors:generate` to regenerate.\n\n"
        f"export type ErrorCode =\n  | {'\n  | '.join(f'"{code}"' for code in sorted_codes)};\n\n"
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

    # Sort by error code so the JSON output is byte-stable regardless of
    # YAML key order. Without this, reordering keys in errors.yaml would
    # drift `required-keys.json` and trip `task errors:check` (issue #286).
    sorted_codes = sorted(errors.keys())

    keys = list(sorted_codes)
    # Sort param names within each list so reordering param keys in
    # errors.yaml (a semantic no-op — translators treat params as a set)
    # keeps required-keys.json byte-stable (PR #309 review follow-up).
    params_by_key: dict[str, list[str]] = {
        code: sorted(cast("dict[str, str]", errors[code].get("params", {})).keys())
        for code in sorted_codes
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


# ─── CLI entry point (issue #372) ────────────────────────────────────
# Default paths resolved against the error-contracts package root so the
# command "just works" when invoked from packages/error-contracts/, which
# is the working directory both the Taskfile (`dir: packages/error-contracts`)
# and the CI workflow (`working-directory: packages/error-contracts`) use.
# These constants are the single source of truth for default paths — the
# ``scripts.generate_all`` CI shim imports ``main``/``build_parser`` from
# this module, so any future default-path change reaches both entry points
# atomically (PR #499 review: previously each module had its own copy and
# drift was possible).
_ERROR_CONTRACTS_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _ERROR_CONTRACTS_DIR.parents[1]
_DEFAULT_ERRORS_YAML = _ERROR_CONTRACTS_DIR / "errors.yaml"
_DEFAULT_PYTHON_DIR = (
    _REPO_ROOT / "apps" / "backend" / "app" / "exceptions" / "_generated"
)
_DEFAULT_TS_PATH = _ERROR_CONTRACTS_DIR / "src" / "generated.ts"
_DEFAULT_REQUIRED_KEYS_PATH = _ERROR_CONTRACTS_DIR / "src" / "required-keys.json"


def main(
    errors_yaml: Path | None = None,
    python_dir: Path | None = None,
    typescript_path: Path | None = None,
    required_keys_path: Path | None = None,
) -> int:
    """Regenerate Python + TypeScript + required-keys.json artifacts.

    All four parameters default to the production monorepo layout. Tests
    pass explicit ``tmp_path`` fixtures; a plain ``python -m scripts.generate``
    call with no arguments drives the real file locations from the defaults
    above.

    Returns 0 on success. Raises ``ValueError`` (from ``load_and_validate``)
    on malformed ``errors.yaml``; the caller's shell propagates the
    non-zero exit. We deliberately do not catch and convert to a return
    code — a malformed ``errors.yaml`` is a developer-facing bug and the
    raw traceback is the most useful signal.

    The canonical entry point for both local (``task errors:generate``,
    issue #372) and CI (``python -m scripts.generate_all``, issue #365)
    flows. ``scripts.generate_all.main`` delegates here so the two paths
    mechanically share default paths + argparse wiring and cannot drift
    (PR #499 review).
    """
    errors_path = errors_yaml if errors_yaml is not None else _DEFAULT_ERRORS_YAML
    py_dir = python_dir if python_dir is not None else _DEFAULT_PYTHON_DIR
    ts_path = typescript_path if typescript_path is not None else _DEFAULT_TS_PATH
    keys_path = (
        required_keys_path
        if required_keys_path is not None
        else _DEFAULT_REQUIRED_KEYS_PATH
    )

    generate_python(errors_path, py_dir)
    generate_typescript(errors_path, ts_path)
    generate_required_keys(errors_path, keys_path)

    sys.stdout.write("Generated all error contract files\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the shared argparse parser for the generate CLI.

    Exposed (non-underscore) so ``scripts.generate_all`` can reuse the
    exact same parser — the two entry points must accept identical flags
    with identical defaults to preserve the byte-identical guarantee
    between ``task errors:generate`` (local) and the CI "Regenerate
    error contracts" step (see issue #365 and issue #372).

    ``help=`` strings are composed from the ``_DEFAULT_*`` module
    constants so editing a default path can never leave ``--help`` out
    of sync with reality — the drift that PR #499 review flagged.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate Python exception classes, TypeScript types, and "
            "required-keys.json from errors.yaml."
        ),
    )
    parser.add_argument(
        "--errors-yaml",
        type=Path,
        default=None,
        help=f"Path to errors.yaml (defaults to {_DEFAULT_ERRORS_YAML}).",
    )
    parser.add_argument(
        "--python-dir",
        type=Path,
        default=None,
        help=(
            f"Directory to write generated Python exception modules "
            f"(defaults to {_DEFAULT_PYTHON_DIR}). "
            "Override is intended for tests writing to tmp_path: the "
            "generated __init__.py and _registry.py emit hard-coded "
            "`app.exceptions._generated.*` import paths, so artifacts "
            "written elsewhere won't be importable without renaming."
        ),
    )
    parser.add_argument(
        "--typescript-path",
        type=Path,
        default=None,
        help=(
            f"Path to write the generated TypeScript module "
            f"(defaults to {_DEFAULT_TS_PATH})."
        ),
    )
    parser.add_argument(
        "--required-keys-path",
        type=Path,
        default=None,
        help=(
            f"Path to write required-keys.json "
            f"(defaults to {_DEFAULT_REQUIRED_KEYS_PATH})."
        ),
    )
    return parser


def _parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    sys.exit(
        main(
            errors_yaml=args.errors_yaml,
            python_dir=args.python_dir,
            typescript_path=args.typescript_path,
            required_keys_path=args.required_keys_path,
        )
    )

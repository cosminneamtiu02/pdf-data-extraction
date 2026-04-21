"""Single entry point for regenerating every error-contracts artifact.

Extracted from the inlined ``python -c '...'`` blocks that previously
lived in ``Taskfile.yml`` (the ``errors:generate`` task) and in
``.github/workflows/ci.yml`` (the "Regenerate error contracts" step).
Those two copies were near-identical but drifted independently; see
issue #365 for the duplication diagnosis.

Both callers now invoke::

    uv run --with pyyaml python -m scripts.generate_all

which regenerates all three output families from ``errors.yaml``
(Python exception classes, TypeScript types, JSON translation
required-keys). Each generator independently re-reads and re-validates
``errors.yaml``; the wrapper does not share the parsed result across
calls. That redundancy is cheap at the current size of ``errors.yaml``
and keeps each generator usable standalone from ``generate.py``. Paths
default to the monorepo layout relative to this script's location, so
the command works with no arguments as long as it runs from
``packages/error-contracts/`` (the Taskfile + CI working directory).
``--errors-yaml`` and the three output-path flags exist for tests that
need to point at fixtures.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.generate import (
    generate_python,
    generate_required_keys,
    generate_typescript,
)

# Default paths resolved against the error-contracts package root so the
# command "just works" when invoked from packages/error-contracts/, which
# is the working directory both the Taskfile (`dir: packages/error-contracts`)
# and the CI workflow (`working-directory: packages/error-contracts`) use.
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
    pass explicit ``tmp_path`` fixtures; the Taskfile and CI call with
    no arguments so defaults drive the real file locations.

    Returns 0 on success. Raises ``ValueError`` (from ``load_and_validate``)
    on malformed ``errors.yaml``; the caller's shell propagates the
    non-zero exit. We deliberately do not catch and convert to a return
    code — a malformed ``errors.yaml`` is a developer-facing bug and the
    raw traceback is the most useful signal.
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


def _parse_args(argv: list[str]) -> argparse.Namespace:
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
        help="Path to errors.yaml (defaults to packages/error-contracts/errors.yaml).",
    )
    parser.add_argument(
        "--python-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write generated Python exception modules "
            "(defaults to apps/backend/app/exceptions/_generated). "
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
            "Path to write the generated TypeScript module "
            "(defaults to packages/error-contracts/src/generated.ts)."
        ),
    )
    parser.add_argument(
        "--required-keys-path",
        type=Path,
        default=None,
        help=(
            "Path to write required-keys.json "
            "(defaults to packages/error-contracts/src/required-keys.json)."
        ),
    )
    return parser.parse_args(argv)


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

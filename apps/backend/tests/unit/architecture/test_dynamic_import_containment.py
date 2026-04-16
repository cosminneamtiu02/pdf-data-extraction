"""AST-scan enforcement for rules that import-linter cannot express.

import-linter only sees static imports via AST analysis. This module covers
two gaps:

1. **C1 real enforcement** - extraction must not import from sibling
   features. The independence contract with one module is vacuously true,
   so this AST scan is the actual gate. It catches both static
   (`from app.features.other import X`) and dynamic
   (`importlib.import_module("app.features.other")`) imports.

2. **Dynamic import containment** - `importlib.import_module("docling")`,
   `importlib.import_module("pymupdf")`, etc. bypass import-linter's
   static graph. This test walks every .py file under `app/` and asserts
   that dynamic imports of contained third-party packages only appear in
   their designated files.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from ._linter_subprocess import BACKEND_DIR

_APP_ROOT: Final[Path] = BACKEND_DIR / "app"
_EXTRACTION_ROOT: Final[Path] = _APP_ROOT / "features" / "extraction"

_CONTAINED_PACKAGES: Final[dict[str, frozenset[str]]] = {
    "docling": frozenset({"docling_document_parser.py"}),
    "pymupdf": frozenset({"pdf_annotator.py", "docling_document_parser.py"}),
    "fitz": frozenset({"pdf_annotator.py", "docling_document_parser.py"}),
    "langextract": frozenset({"extraction_engine.py", "ollama_gemma_provider.py"}),
    "httpx": frozenset({"ollama_gemma_provider.py", "ollama_health_probe.py"}),
}


def _collect_static_root_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _collect_static_dotted_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module)
    return names


def _collect_dynamic_import_targets(source: str) -> set[str]:
    """Find string-literal arguments to importlib.import_module calls."""
    tree = ast.parse(source)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_import_module = False
        if (isinstance(func, ast.Attribute) and func.attr == "import_module") or (
            isinstance(func, ast.Name) and func.id == "import_module"
        ):
            is_import_module = True
        if is_import_module and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                targets.add(arg.value.split(".")[0])
    return targets


def test_extraction_does_not_import_from_sibling_features() -> None:
    """C1 real enforcement: no file under extraction/ may import app.features.<non-extraction>.

    The import-linter C1 independence contract is a placeholder (single-module
    independence is vacuously true). This AST scan is the actual gate.
    """
    offenders: list[str] = []
    for py_file in _EXTRACTION_ROOT.rglob("*.py"):
        source = py_file.read_text()
        for dotted in _collect_static_dotted_imports(source):
            if dotted.startswith("app.features.") and not dotted.startswith(
                "app.features.extraction"
            ):
                rel = str(py_file.relative_to(_EXTRACTION_ROOT))
                offenders.append(f"{rel} statically imports {dotted}")
        for target in _collect_dynamic_import_targets(source):
            if target.startswith("app.features.") and not target.startswith(
                "app.features.extraction"
            ):
                rel = str(py_file.relative_to(_EXTRACTION_ROOT))
                offenders.append(f"{rel} dynamically imports {target}")

    assert not offenders, (
        "C1 violation: extraction feature imports from sibling feature(s):\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


_DYNAMIC_CONTAINMENT_CASES = [(pkg, allowed) for pkg, allowed in _CONTAINED_PACKAGES.items()]


@pytest.mark.parametrize(
    ("package", "allowed_files"),
    _DYNAMIC_CONTAINMENT_CASES,
    ids=[pkg for pkg, _ in _DYNAMIC_CONTAINMENT_CASES],
)
def test_dynamic_imports_of_contained_packages_are_allowlisted(
    package: str,
    allowed_files: frozenset[str],
) -> None:
    """Dynamic import containment: importlib.import_module("<package>") only in allowed files.

    import-linter cannot see importlib.import_module calls. This AST scan
    catches them and asserts they only appear in the designated files.
    Scoped to all of `app/` (not just extraction) to match the app-wide
    source_modules = app used by the C3-C6 contracts.
    """
    offenders: list[str] = []
    for py_file in _APP_ROOT.rglob("*.py"):
        if py_file.name in allowed_files:
            continue
        targets = _collect_dynamic_import_targets(py_file.read_text())
        if package in targets:
            rel = str(py_file.relative_to(_APP_ROOT))
            offenders.append(rel)

    assert not offenders, (
        f"Dynamic import of '{package}' found outside allowed files "
        f"{sorted(allowed_files)}:\n" + "\n".join(f"  - {o}" for o in offenders)
    )


@pytest.mark.parametrize(
    ("package", "allowed_files"),
    _DYNAMIC_CONTAINMENT_CASES,
    ids=[f"{pkg}-static" for pkg, _ in _DYNAMIC_CONTAINMENT_CASES],
)
def test_static_imports_of_contained_packages_are_allowlisted(
    package: str,
    allowed_files: frozenset[str],
) -> None:
    """Static import containment belt-and-braces: import <package> only in allowed files.

    This is the companion to the import-linter C3-C6 forbidden contracts.
    It adds coverage for the full `app/` tree (matching source_modules = app)
    and catches edge cases where a static import sneaks through between
    lint-imports runs.
    """
    offenders: list[str] = []
    for py_file in _APP_ROOT.rglob("*.py"):
        if py_file.name in allowed_files:
            continue
        roots = _collect_static_root_imports(py_file.read_text())
        if package in roots:
            rel = str(py_file.relative_to(_APP_ROOT))
            offenders.append(rel)

    assert not offenders, (
        f"Static import of '{package}' found outside allowed files "
        f"{sorted(allowed_files)}:\n" + "\n".join(f"  - {o}" for o in offenders)
    )

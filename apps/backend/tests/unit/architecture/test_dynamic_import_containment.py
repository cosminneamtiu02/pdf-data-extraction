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
    # Docling containment expanded from a single file to the set of
    # parsing/* files introduced by the issue #159 refactor.
    "docling": frozenset(
        {
            "docling_document_parser.py",
            "_real_docling_converter_adapter.py",
            "_real_docling_document_adapter.py",
        },
    ),
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
    """Find string-literal arguments to dynamic-import calls.

    Covers both dynamic-import mechanisms named in this module's docstring:
    ``importlib.import_module(...)`` and the ``__import__`` builtin. Missing
    either one lets a file reach a contained package without import-linter
    or this AST scan firing — ``__import__`` is an expression, not an
    ``import`` statement, so import-linter cannot see it.

    Returns the FULL dotted target (e.g. `"app.features.billing.foo"` or
    `"docling.datamodel.base_models"`), not just the root module. Storing
    only the root silently neutralized the C1 sibling-feature guard at
    `test_extraction_does_not_import_from_sibling_features`, whose
    `startswith("app.features.")` predicate could never match `"app"`.
    Downstream callers that want to match a root package must use a
    dotted-boundary prefix check (see `_target_matches_package`).
    """
    tree = ast.parse(source)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_dynamic_import = False
        if (isinstance(func, ast.Attribute) and func.attr == "import_module") or (
            isinstance(func, ast.Name) and func.id in {"import_module", "__import__"}
        ):
            is_dynamic_import = True
        if is_dynamic_import and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                targets.add(arg.value)
    return targets


def _target_matches_package(target: str, package: str) -> bool:
    """Check whether a dotted dynamic-import target belongs to `package`.

    Matches `package` itself and any submodule (`package.X.Y`), but not
    lookalikes like `packageish`. Companion to `_collect_dynamic_import_targets`
    now that it returns full dotted paths.
    """
    return target == package or target.startswith(package + ".")


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
        if any(_target_matches_package(target, package) for target in targets):
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


def test_collect_dynamic_import_targets_records_full_dotted_path() -> None:
    """Direct unit test: the collector must store the full dotted target, not just the root.

    Before the fix, `_collect_dynamic_import_targets` stored only the root
    module name (e.g. `"app"` for `importlib.import_module("app.features.billing.foo")`).
    That made the C1 sibling-feature guard's `startswith("app.features.")`
    predicate always False, silently disabling enforcement. The collector
    must store the whole dotted path so the downstream `startswith` / prefix
    checks can match meaningfully.
    """
    source = 'import importlib\nimportlib.import_module("app.features.billing.foo")\n'
    targets = _collect_dynamic_import_targets(source)
    assert "app.features.billing.foo" in targets
    assert targets == {"app.features.billing.foo"}


def test_c1_check_catches_dynamic_sibling_feature_import() -> None:
    """Regression: the C1 guard's predicate fires on a dynamic sibling-feature import.

    Synthesizes the collector output for a hypothetical extraction-tree file
    that does `importlib.import_module("app.features.billing.foo")` and
    asserts the same sibling-feature-exclusion predicate used by
    `test_extraction_does_not_import_from_sibling_features` would flag it.
    Before the fix, targets was `{"app"}` and the predicate always
    evaluated to False, so the guard could never fire.
    """
    source = 'import importlib\nimportlib.import_module("app.features.billing.foo")\n'
    targets = _collect_dynamic_import_targets(source)

    offenders = [
        target
        for target in targets
        if target.startswith("app.features.") and not target.startswith("app.features.extraction")
    ]

    assert offenders == ["app.features.billing.foo"], (
        f"C1 dynamic-sibling-feature guard predicate did not fire; targets={targets!r}"
    )


def test_collect_dynamic_import_targets_preserves_root_only_target() -> None:
    """The collector still records plain root-level dynamic imports verbatim.

    `importlib.import_module("docling")` must show up as `"docling"` (not
    lost) so the third-party containment parametrized tests keep working.
    """
    source = 'import importlib\nimportlib.import_module("docling")\n'
    targets = _collect_dynamic_import_targets(source)
    assert targets == {"docling"}


def test_collect_dynamic_import_targets_records_builtin___import__() -> None:
    """The collector must also catch the ``__import__`` builtin.

    ``__import__("docling")`` is a second dynamic-import mechanism named in
    this module's docstring alongside ``importlib.import_module``. Missing it
    lets a file use the builtin to reach a contained package (Docling,
    PyMuPDF, LangExtract, httpx-to-Ollama) without any containment test
    firing — import-linter cannot see the call either, since it is an
    expression, not an ``import`` statement.
    """
    source = '__import__("docling")\n'
    targets = _collect_dynamic_import_targets(source)
    assert targets == {"docling"}


def test_collect_dynamic_import_targets_records_builtin___import___with_dotted_path() -> None:
    """The ``__import__`` branch must record the FULL dotted target.

    Mirrors the ``importlib.import_module`` invariant — storing only the
    root would neutralise the C1 sibling-feature guard's
    ``startswith("app.features.")`` predicate.
    """
    source = '__import__("app.features.billing.foo")\n'
    targets = _collect_dynamic_import_targets(source)
    assert targets == {"app.features.billing.foo"}

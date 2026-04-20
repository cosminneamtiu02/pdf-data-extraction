"""AST-scan enforcement for rules that import-linter cannot express.

import-linter only sees static imports via AST analysis. This module covers
three gaps:

1. **C1 real enforcement** - extraction must not import from sibling
   features. The independence contract with one module is vacuously true,
   so this AST scan is the actual gate. It catches both static
   (`from app.features.other import X`) and dynamic
   (`importlib.import_module("app.features.other")`) imports.

2. **Dynamic import containment** - `importlib.import_module("docling")`,
   `importlib.import_module("pymupdf")`, the equivalent `__import__`
   builtin form (e.g. `__import__("docling")`), and
   `importlib.util.find_spec("langextract")` all bypass import-linter's
   static graph. This test walks every .py file under `app/` and asserts
   that dynamic imports (or availability probes) of contained third-party
   packages only appear in their designated files.

3. **Composition-root containment for `app/api/`** (issue #229) - the
   `shared-no-features` import-linter contract covers `app.shared`,
   `app.core`, and `app.schemas` but not `app.api`, because `app.api`
   contains composition-root files (`deps.py`, `health_router.py`,
   `probe_cache.py`) that legitimately import from `app.features.extraction.*`
   to wire DI factories and type-annotate feature handles. `app/main.py`
   is the other composition point (the FastAPI app factory: builds the
   startup probe, loads skills, and includes the extraction router) and
   is therefore also outside the scope of the `app/api/` gate. Without
   a mechanical gate on `app/api/`, any future file under that package
   could silently acquire feature-internal imports and erode the
   "composition root is an exception, not a free pass" invariant. This
   AST scan asserts feature imports only appear in the authorized
   composition-root files under `app/api/`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

from ._linter_subprocess import BACKEND_DIR

_APP_ROOT: Final[Path] = BACKEND_DIR / "app"
_EXTRACTION_ROOT: Final[Path] = _APP_ROOT / "features" / "extraction"
_API_ROOT: Final[Path] = _APP_ROOT / "api"

# Composition-root allowlist (issue #229). Only these files under `app/api/`
# may statically or dynamically import from `app.features.*`. They wire the DI
# graph from features into the FastAPI app and are the `app/api/`-scoped
# exception to the "modules outside features do not reach into features"
# invariant. `app/main.py` (the FastAPI app factory) is the other composition
# point outside `app/features/` and is out of scope of this gate because this
# scan only walks `app/api/`. Every other file under `app/api/` (middleware,
# request-id, schemas, exception handlers, ...) must stay feature-agnostic.
#
# `probe_cache.py` is included because it type-annotates against
# `OllamaHealthProbe` (a feature type) under a `TYPE_CHECKING` guard. The AST
# collector does not distinguish TYPE_CHECKING-guarded from runtime imports
# (by design, to match the existing C1 sibling-feature scan), so the cache's
# feature-type coupling shows up as a feature import. Semantically the cache
# is composition-root-adjacent wiring (it wraps a feature object and
# delegates `.check()` calls), so allowlisting it is correct.
#
# Entries are RELATIVE POSIX paths from `_API_ROOT`, not basenames. Keying on
# basename would let a file at `app/api/schemas/deps.py` evade the gate by
# sharing the name `deps.py` with the composition-root file; keying on the
# relative path pins the allowlist to the exact three top-level files.
_API_COMPOSITION_ROOT_FILES: Final[frozenset[str]] = frozenset(
    {
        "deps.py",
        "health_router.py",
        "probe_cache.py",
    },
)

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
    "langextract": frozenset(
        {
            "extraction_engine.py",
            "_validating_langextract_adapter.py",
            "ollama_gemma_provider.py",
        },
    ),
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


def _importlib_aliases_from_module(tree: ast.Module) -> frozenset[str]:
    """Return local names bound to the ``importlib`` package at module scope.

    Scans only ``tree.body`` (module-level statements), NOT nested function
    or class bodies, because imports inside a function don't bind a
    module-scope name. Picks up three shapes:

    * ``import importlib`` → binds ``importlib``
    * ``import importlib as il`` → binds ``il``
    * ``import importlib.util`` → binds ``importlib`` (Python's standard
      package-import semantics — the root package name is always bound)

    PR #310 review follow-up: tracking aliased bindings is load-bearing
    because ``import importlib as il; il.util.find_spec("pkg")`` was a
    silent bypass of the containment gate under the literal-match-only
    logic. An ``import importlib.util as u`` alias binds ``u`` to the
    submodule (not to ``importlib``), so it is intentionally ignored
    here — the submodule alias does not give access to
    ``u.util.find_spec`` (it would be ``u.find_spec`` directly, which
    currently does not have a containment-detection pattern).
    """
    aliases: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            if alias.name == "importlib":
                aliases.add(alias.asname if alias.asname is not None else "importlib")
            elif alias.name.startswith("importlib.") and alias.asname is None:
                # `import importlib.util` (no alias) binds `importlib` at
                # module scope. `import importlib.util as u` binds `u`
                # to the submodule, not to `importlib`, so skip when an
                # asname is present.
                aliases.add("importlib")
    return frozenset(aliases)


def _find_spec_bindings_from_importlib_util(tree: ast.Module) -> frozenset[str]:
    """Return local names bound to ``importlib.util.find_spec`` at module scope.

    Scans only ``tree.body`` (module-level statements), NOT nested scopes.
    An ``ImportFrom`` inside a function body does not create a module-level
    name binding, so walking the full tree (``ast.walk``) would falsely
    flag calls to the module-scope ``find_spec`` symbol when the
    import lives in a nested block.

    Includes aliased forms so ``from importlib.util import find_spec as fs``
    records ``fs`` as a detected target (PR #310 review follow-up). Without
    this, ``fs("langextract")`` was a silent bypass of the gate.

    Limitation: if a source imports ``find_spec`` and later rebinds the
    local name (``fs = something_else``), the walker still counts calls
    to that imported name as containment targets. Local reassignment of
    an import-bound name is rare enough that this remains an accepted
    first-pass narrowing; documenting it here keeps the trade-off
    visible for future readers.
    """
    bound_names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "importlib.util" or node.level != 0:
            continue
        for alias in node.names:
            if alias.name != "find_spec":
                continue
            bound_names.add(alias.asname if alias.asname is not None else "find_spec")
    return frozenset(bound_names)


def _is_importlib_util_find_spec_attribute(
    func: ast.expr,
    *,
    importlib_aliases: frozenset[str],
) -> bool:
    """Return True iff ``func`` is an attribute chain ``<alias>.util.find_spec``.

    Walks the ``ast.Attribute`` chain and matches the three-segment path
    ``<alias>.util.find_spec`` for every ``<alias>`` in ``importlib_aliases``.
    Over-broad matching (any call whose attribute name is ``find_spec``)
    would flag unrelated objects that expose a ``.find_spec(...)`` method —
    e.g. a custom importer's ``obj.find_spec(...)`` — and produce false
    positives for the dynamic-import containment gate.

    PR #310 review follow-up: the alias set is threaded through so that
    ``import importlib as il; il.util.find_spec(...)`` is recognised.
    """
    if not isinstance(func, ast.Attribute) or func.attr != "find_spec":
        return False
    util = func.value
    if not isinstance(util, ast.Attribute) or util.attr != "util":
        return False
    importlib_name = util.value
    return isinstance(importlib_name, ast.Name) and importlib_name.id in importlib_aliases


def _collect_dynamic_import_targets(source: str) -> set[str]:
    """Find string-literal arguments to dynamic-import calls.

    Covers the three dynamic-import-adjacent mechanisms that bypass
    import-linter's static graph:

    * ``importlib.import_module(...)`` — the canonical runtime import.
    * ``__import__(...)`` — the builtin, an expression rather than an
      ``import`` statement.
    * ``importlib.util.find_spec(...)`` — probes module availability so a
      caller can conditionally branch on whether a contained third-party
      package is installed. Missing it lets a file stealth-escape the C1
      gate by writing ``if find_spec("langextract") is not None: ...``
      without tripping either import-linter or the dynamic-import branches
      above.

    ``find_spec`` matching is narrowed (PR #310 review follow-up) to avoid
    false positives against unrelated callables that happen to share the
    name:

    * For the attribute form, only the exact chain
      ``importlib.util.find_spec`` is matched; ``obj.find_spec(...)`` on
      any other object is ignored.
    * For the bare-name form, the source must also contain a top-level
      ``from importlib.util import find_spec`` binding for the call to
      count as a containment target. A module that defines a local
      ``find_spec`` function (or imports one from a different package) is
      not flagged.

    Returns the FULL dotted target (e.g. `"app.features.billing.foo"` or
    `"docling.datamodel.base_models"`), not just the root module. Storing
    only the root silently neutralized the C1 sibling-feature guard at
    `test_extraction_does_not_import_from_sibling_features`, whose
    `startswith("app.features.")` predicate could never match `"app"`.
    Downstream callers that want to match a root package must use a
    dotted-boundary prefix check (see `_target_matches_package`).
    """
    tree = ast.parse(source)
    importlib_aliases = _importlib_aliases_from_module(tree)
    find_spec_local_bindings = _find_spec_bindings_from_importlib_util(tree)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not _call_is_dynamic_import(
            func,
            importlib_aliases=importlib_aliases,
            find_spec_local_bindings=find_spec_local_bindings,
        ):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            targets.add(arg.value)
    return targets


def _call_is_dynamic_import(
    func: ast.expr,
    *,
    importlib_aliases: frozenset[str],
    find_spec_local_bindings: frozenset[str],
) -> bool:
    """Return True iff ``func`` names one of the tracked dynamic-import callables.

    Handles the callable shapes covered by the walker:

    * ``<anything>.import_module(...)`` (attribute call; the ``importlib``
      root is not verified because the canonical pattern
      ``importlib.import_module`` is by far the dominant use and a false
      positive is harmless for containment purposes — any string-literal
      argument is still gated by the per-package allowlist),
    * the attribute chain ``<alias>.util.find_spec(...)`` where
      ``<alias>`` is any local name bound to the ``importlib`` package
      (PR #310 review follow-up: ``import importlib as il; il.util.
      find_spec(...)`` is now recognised),
    * bare ``import_module(...)`` or ``__import__(...)`` calls, and
    * bare ``<name>(...)`` calls where ``<name>`` is any module-scope
      binding of ``importlib.util.find_spec`` — covers the canonical
      ``from importlib.util import find_spec`` AND the aliased
      ``from importlib.util import find_spec as fs`` form (PR #310
      review follow-up: the aliased form was previously a bypass).
    """
    if isinstance(func, ast.Attribute):
        if func.attr == "import_module":
            return True
        return _is_importlib_util_find_spec_attribute(func, importlib_aliases=importlib_aliases)
    if isinstance(func, ast.Name):
        if func.id in {"import_module", "__import__"}:
            return True
        if func.id in find_spec_local_bindings:
            return True
    return False


def _target_matches_package(target: str, package: str) -> bool:
    """Check whether a dotted dynamic-import target belongs to `package`.

    Matches `package` itself and any submodule (`package.X.Y`), but not
    lookalikes like `packageish`. Companion to `_collect_dynamic_import_targets`
    now that it returns full dotted paths.
    """
    return target == package or target.startswith(package + ".")


def _is_api_composition_root_file(py_file: Path, api_root: Path) -> bool:
    """Return True iff `py_file` is an allowlisted composition-root file.

    Extracted so both the filesystem walk
    (``test_api_feature_imports_are_confined_to_composition_root``) and the
    synthetic subdir-namesake regression test
    (``test_api_composition_root_allowlist_rejects_subdir_namesake``) invoke the
    SAME predicate. Keeping the real walk and the synthetic test coupled to one
    helper means any regression to basename-keyed allowlisting fails both at
    once; a prior draft used a locally-defined ``allowlist`` in the synthetic
    test that could stay green even if the production scan regressed.

    The allowlist is keyed on the RELATIVE POSIX path from ``api_root`` (not
    the basename) so a file at ``app/api/schemas/deps.py`` cannot evade the
    gate by colliding basenames with the top-level ``app/api/deps.py``.
    """
    rel = py_file.relative_to(api_root).as_posix()
    return rel in _API_COMPOSITION_ROOT_FILES


def test_extraction_does_not_import_from_sibling_features() -> None:
    """C1 real enforcement: no file under extraction/ may import app.features.<non-extraction>.

    The import-linter C1 independence contract is a placeholder (single-module
    independence is vacuously true). This AST scan is the actual gate.
    """
    offenders: list[str] = []
    for py_file in _EXTRACTION_ROOT.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
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
        targets = _collect_dynamic_import_targets(py_file.read_text(encoding="utf-8"))
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
        roots = _collect_static_root_imports(py_file.read_text(encoding="utf-8"))
        if package in roots:
            rel = str(py_file.relative_to(_APP_ROOT))
            offenders.append(rel)

    assert not offenders, (
        f"Static import of '{package}' found outside allowed files "
        f"{sorted(allowed_files)}:\n" + "\n".join(f"  - {o}" for o in offenders)
    )


def test_api_feature_imports_are_confined_to_composition_root() -> None:
    """Issue #229: feature imports under `app/api/` are allowed only in the composition-root files.

    The import-linter `shared-no-features` contract lists `app.shared`,
    `app.core`, and `app.schemas` but deliberately omits `app.api` because
    `app/api/deps.py`, `app/api/health_router.py`, and `app/api/probe_cache.py`
    are the composition root and must reach into features to wire the DI
    graph and type-annotate feature handles. Adding `app.api` to that
    contract's `source_modules` would break `task check` immediately, but
    leaving it off with no replacement gate lets a future refactor silently
    introduce feature imports into, for example, `app/api/middleware.py` or
    `app/api/request_id_middleware.py` — eroding the composition-root
    exception into a free pass.

    This test walks every .py file under `app/api/`, and for every file
    NOT in the composition-root allowlist, asserts that it contains no
    static or dynamic import of `app.features` (the bare package) or
    `app.features.*` (any submodule). Adding a new file to `app/api/` that
    needs feature imports requires a PR that expands
    `_API_COMPOSITION_ROOT_FILES` above, which is a code-review signal
    that the composition-root boundary is being widened deliberately.

    Allowlist entries are RELATIVE POSIX paths from `_API_ROOT` (not
    basenames), so a file at `app/api/schemas/deps.py` cannot bypass the
    gate by sharing a basename with the composition-root `deps.py`. Feature
    imports are matched with a dotted-boundary predicate
    (`_target_matches_package`) so a bare `import app.features` collected
    as the dotted target `"app.features"` is caught alongside
    `"app.features.X"` submodule imports.
    """
    offenders: list[str] = []
    for py_file in _API_ROOT.rglob("*.py"):
        if _is_api_composition_root_file(py_file, _API_ROOT):
            continue
        rel = py_file.relative_to(_API_ROOT).as_posix()
        source = py_file.read_text(encoding="utf-8")
        offenders.extend(
            f"{rel} statically imports {dotted}"
            for dotted in _collect_static_dotted_imports(source)
            if _target_matches_package(dotted, "app.features")
        )
        offenders.extend(
            f"{rel} dynamically imports {target}"
            for target in _collect_dynamic_import_targets(source)
            if _target_matches_package(target, "app.features")
        )

    assert not offenders, (
        "Feature imports outside the `app/api/` composition root are forbidden. "
        f"Allowed files: {sorted(_API_COMPOSITION_ROOT_FILES)}. Offenders:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_api_composition_root_predicate_flags_a_synthetic_offender() -> None:
    """Issue #229: the predicate must fire on a synthetic non-allowlisted source that imports features.

    Companion to ``test_api_feature_imports_are_confined_to_composition_root``.
    The filesystem-walking test passes vacuously if every file under
    ``app/api/`` today happens to avoid feature imports (either because it
    is allowlisted or because it genuinely has none), which would mask a
    broken predicate. This test feeds the collectors a synthetic source
    string that models ``app/api/middleware.py`` gaining a feature import
    and asserts the same predicates used above would flag both the static
    and the dynamic form. Red-fail if someone weakens the collectors or
    the startswith check to the point where the real walk could not
    detect a regression.
    """
    source = (
        "from app.features.extraction.skills import SkillManifest  # noqa: F401\n"
        'import importlib\nimportlib.import_module("app.features.extraction.service")\n'
    )

    static_offenders = [
        dotted
        for dotted in _collect_static_dotted_imports(source)
        if _target_matches_package(dotted, "app.features")
    ]
    dynamic_offenders = [
        target
        for target in _collect_dynamic_import_targets(source)
        if _target_matches_package(target, "app.features")
    ]

    assert "app.features.extraction.skills" in static_offenders, (
        f"static-import predicate did not fire on synthetic offender: {static_offenders!r}"
    )
    assert "app.features.extraction.service" in dynamic_offenders, (
        f"dynamic-import predicate did not fire on synthetic offender: {dynamic_offenders!r}"
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


def test_collect_dynamic_import_targets_records_util_find_spec() -> None:
    """The collector must also catch ``importlib.util.find_spec`` (issue #280).

    ``importlib.util.find_spec("langextract")`` is a third dynamic-import-
    adjacent mechanism: it probes module availability so a caller can
    conditionally branch on whether a contained third-party package is
    installed. A production file using it to reach Docling / PyMuPDF /
    LangExtract / httpx would slip through both import-linter (the call
    is an expression, not an ``import`` statement) and the previous AST
    scan (which only looked for ``import_module`` and ``__import__``),
    stealth-escaping the C1 gate. Treat the first string-literal argument
    to ``find_spec`` as a containment target, mirroring the ``__import__``
    and ``import_module`` handling.
    """
    source = 'import importlib.util\nimportlib.util.find_spec("langextract")\n'
    targets = _collect_dynamic_import_targets(source)
    assert "langextract" in targets


def test_collect_dynamic_import_targets_records_util_find_spec_with_dotted_path() -> None:
    """The ``find_spec`` branch must record the FULL dotted target (issue #280).

    Mirrors the ``importlib.import_module`` and ``__import__`` invariants —
    storing only the root would neutralise any sibling-feature guard whose
    predicate uses a dotted-prefix check.
    """
    source = 'import importlib.util\nimportlib.util.find_spec("app.features.billing.foo")\n'
    targets = _collect_dynamic_import_targets(source)
    assert targets == {"app.features.billing.foo"}


def test_collect_dynamic_import_targets_records_direct_find_spec_import() -> None:
    """The collector must also catch the direct-import form of ``find_spec``.

    ``from importlib.util import find_spec`` followed by a bare
    ``find_spec("langextract")`` call reaches the symbol by ``ast.Name`` rather
    than ``ast.Attribute``; treating the attribute form alone leaves an easy
    containment-gate bypass. Mirrors the ``__import__`` / bare ``import_module``
    branch already handled by the same walker. (PR #310 review follow-up.)
    """
    source = 'from importlib.util import find_spec\nfind_spec("langextract")\n'
    targets = _collect_dynamic_import_targets(source)
    assert "langextract" in targets


def test_collect_dynamic_import_targets_records_direct_find_spec_with_dotted_path() -> None:
    """Direct-import ``find_spec`` must also store the FULL dotted target.

    Same invariant as the attribute form — storing only the package root would
    let a containment rule whose predicate checks for ``app.features.<X>``
    silently pass a probe against a sibling feature's submodule.
    """
    source = 'from importlib.util import find_spec\nfind_spec("app.features.billing.foo")\n'
    targets = _collect_dynamic_import_targets(source)
    assert targets == {"app.features.billing.foo"}


def test_collect_dynamic_import_targets_ignores_local_find_spec_function() -> None:
    """Bare ``find_spec(...)`` calls must be ignored when the name is not bound to ``importlib.util.find_spec``.

    Copilot review feedback on PR #310: the previous walker matched ``ast.Name(id="find_spec")``
    unconditionally, which over-approximates the intent (detect
    ``importlib.util.find_spec``) and produces false positives any time an
    unrelated local function named ``find_spec`` is called. Narrow to
    treat a bare ``find_spec(...)`` call as a containment target ONLY when
    ``find_spec`` is imported from ``importlib.util`` in the same source.
    """
    source = 'def find_spec(name: str) -> None:\n    return None\nfind_spec("langextract")\n'
    targets = _collect_dynamic_import_targets(source)
    assert "langextract" not in targets


def test_collect_dynamic_import_targets_ignores_method_call_find_spec_on_other_object() -> None:
    """Attribute-form ``obj.find_spec(...)`` must be ignored unless the chain is ``importlib.util.find_spec``.

    Copilot review feedback on PR #310: the previous walker matched any
    ``ast.Attribute`` call with ``attr == "find_spec"``, which flags unrelated
    objects that happen to expose a ``.find_spec(...)`` method. The narrowed
    rule is to require the full three-segment chain ``importlib.util.find_spec``
    — ``func.attr == "find_spec"``, ``func.value`` is ``ast.Attribute`` with
    ``attr == "util"``, and ``func.value.value`` is ``ast.Name(id="importlib")``.
    """
    source = (
        "class Finder:\n"
        "    def find_spec(self, name: str) -> None:\n"
        "        return None\n"
        "obj = Finder()\n"
        'obj.find_spec("langextract")\n'
    )
    targets = _collect_dynamic_import_targets(source)
    assert "langextract" not in targets


def test_collect_dynamic_import_targets_records_aliased_importlib_attribute() -> None:
    """``import importlib as il; il.util.find_spec(...)`` must be detected.

    PR #310 review feedback: restricting the attribute-form detector to
    the literal ``importlib.util.find_spec`` chain left an easy bypass —
    renaming the ``importlib`` root binding via
    ``import importlib as il`` made the same availability probe invisible
    to the scanner. Tracking ``Import`` bindings at module scope fixes
    this: the aliased root feeds into the attribute-chain matcher.
    """
    source = 'import importlib as il\nil.util.find_spec("langextract")\n'
    targets = _collect_dynamic_import_targets(source)
    assert "langextract" in targets


def test_collect_dynamic_import_targets_records_aliased_from_import_find_spec() -> None:
    """``from importlib.util import find_spec as fs; fs(...)`` must be detected.

    PR #310 review feedback: requiring the local binding name to be
    literally ``find_spec`` left a trivial bypass — aliasing the import
    (``as fs``) meant ``fs("langextract")`` sailed past the scanner.
    Tracking every local binding name from the ``ImportFrom`` and
    matching bare calls to any of those names closes the hole.
    """
    source = 'from importlib.util import find_spec as fs\nfs("langextract")\n'
    targets = _collect_dynamic_import_targets(source)
    assert "langextract" in targets


def test_collect_dynamic_import_targets_ignores_nested_find_spec_import() -> None:
    """An ``ImportFrom`` inside a function body does NOT bind a module-scope
    name. A bare ``find_spec("pkg")`` called elsewhere must not be flagged
    unless the module binds ``find_spec`` at module scope.

    PR #310 review feedback: ``_find_spec_bindings_from_importlib_util``
    used to call ``ast.walk`` and descend into function/class bodies,
    which incorrectly treated a nested import as a module-scope binding.
    The fix restricts the scan to ``tree.body``.

    In this source, the bare ``find_spec("langextract")`` that runs at
    module scope is an unbound reference at runtime (NameError), so the
    containment scan must NOT claim the call targets ``langextract``.
    """
    source = (
        "def helper():\n"
        "    from importlib.util import find_spec\n"
        "    return find_spec\n"
        'find_spec("langextract")\n'
    )
    targets = _collect_dynamic_import_targets(source)
    assert "langextract" not in targets


def test_api_composition_root_predicate_flags_bare_app_features_package_import() -> None:
    """Issue #229: the predicate must also fire on bare-package feature imports.

    A plain ``import app.features`` or ``from app.features import extraction``
    gets collected as the dotted target ``"app.features"`` (no trailing dot),
    which ``startswith("app.features.")`` quietly misses. That lets a non-
    composition-root file under ``app/api/`` reach into ``app.features`` via
    the package-level handle without the guard firing. The fix is to use a
    dotted-boundary check (``_target_matches_package``) that matches
    ``"app.features"`` itself as well as ``"app.features.X"`` submodules.

    This test asserts both the static- and dynamic-import paths detect a bare
    ``app.features`` import using the dotted-boundary predicate.
    """
    source = (
        "from app.features import extraction  # noqa: F401\n"
        'import importlib\nimportlib.import_module("app.features")\n'
    )

    static_targets = _collect_static_dotted_imports(source)
    dynamic_targets = _collect_dynamic_import_targets(source)

    assert "app.features" in static_targets, (
        f"static collector did not see bare `from app.features import ...`: {static_targets!r}"
    )
    assert "app.features" in dynamic_targets, (
        f"dynamic collector did not see bare `import_module('app.features')`: {dynamic_targets!r}"
    )

    static_flagged = [d for d in static_targets if _target_matches_package(d, "app.features")]
    dynamic_flagged = [t for t in dynamic_targets if _target_matches_package(t, "app.features")]

    assert "app.features" in static_flagged, (
        "dotted-boundary predicate did not flag bare `app.features` in static imports"
    )
    assert "app.features" in dynamic_flagged, (
        "dotted-boundary predicate did not flag bare `app.features` in dynamic imports"
    )

    # Regression: the naive `startswith("app.features.")` misses the bare form.
    assert not any(d.startswith("app.features.") for d in ("app.features",)), (
        "sanity check: naive startswith must NOT match the bare package name"
    )


def test_api_composition_root_allowlist_rejects_subdir_namesake(tmp_path: Path) -> None:
    """Issue #229: a subdir file with an allowlisted basename must NOT bypass the gate.

    The original implementation keyed the allowlist on ``py_file.name``, so a
    file at ``app/api/schemas/deps.py`` would evade enforcement by sharing the
    basename ``deps.py`` with the composition-root ``app/api/deps.py``. The
    fix keys the allowlist on the relative path from the api-root
    (``as_posix()``), so only ``deps.py``, ``health_router.py``, and
    ``probe_cache.py`` at the top of ``app/api/`` are allowed to reach into
    features.

    Asserts the REAL production predicate ``_is_api_composition_root_file``
    (the same helper used by
    ``test_api_feature_imports_are_confined_to_composition_root``) classifies
    these synthetic files correctly. If someone regresses the production walk
    back to ``py_file.name`` membership, this test fails alongside the real
    walk, instead of passing vacuously against a locally-defined allowlist.
    """
    api_root = tmp_path / "api"
    (api_root / "schemas").mkdir(parents=True)
    top_level_deps = api_root / "deps.py"
    nested_deps = api_root / "schemas" / "deps.py"
    top_level_deps.write_text("# composition root - feature imports allowed\n", encoding="utf-8")
    nested_deps.write_text(
        "from app.features.extraction.skills import SkillManifest  # noqa: F401\n",
        encoding="utf-8",
    )

    # Sanity: both files share the same basename, so any basename-keyed
    # predicate would accept both.
    assert top_level_deps.name == nested_deps.name == "deps.py"

    # The REAL predicate used by the production walk must accept the top-level
    # file and reject the nested namesake. Coupling the regression test to
    # `_is_api_composition_root_file` means a basename-regression fails this
    # test alongside the filesystem walk rather than staying green against a
    # divergent locally-defined allowlist.
    assert _is_api_composition_root_file(top_level_deps, api_root), (
        "top-level deps.py must be allowlisted by the production predicate"
    )
    assert not _is_api_composition_root_file(nested_deps, api_root), (
        "schemas/deps.py must NOT be allowlisted by the production predicate via basename collision"
    )

    # Belt-and-braces: the allowlist constant itself must contain only the
    # expected relative-POSIX keys, so an accidental basename entry would
    # also be caught here.
    assert _API_COMPOSITION_ROOT_FILES == frozenset(  # noqa: SIM300 - assertion pairs the constant on the left for readability
        {"deps.py", "health_router.py", "probe_cache.py"}
    )

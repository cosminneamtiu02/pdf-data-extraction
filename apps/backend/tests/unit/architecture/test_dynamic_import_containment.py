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
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from ._linter_subprocess import BACKEND_DIR

_APP_ROOT: Final[Path] = BACKEND_DIR / "app"
_SCRIPTS_ROOT: Final[Path] = BACKEND_DIR / "scripts"
_EXTRACTION_ROOT: Final[Path] = _APP_ROOT / "features" / "extraction"
_API_ROOT: Final[Path] = _APP_ROOT / "api"

# Third-party containment is scoped to the whole backend, not just `app/`.
# `scripts/` lives outside `app/` but is still part of the same deployable unit
# and was able to import Docling / PyMuPDF / LangExtract / Ollama-httpx
# without tripping any gate (issue #327 — stealth escape from containment).
# The import-linter contracts in `import-linter-contracts.ini` use
# `source_modules = app` and therefore miss this path too; the AST scan here
# is the mechanical gate that covers `scripts/`. Adding a new top-level
# backend directory (e.g. `tools/`) that ships Python code requires extending
# this tuple alongside a CLAUDE.md update — the scan-roots list is the single
# place that pins the containment boundary for the AST gate.
_CONTAINMENT_ROOTS: Final[tuple[Path, ...]] = (_APP_ROOT, _SCRIPTS_ROOT)

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
    # Entries are RELATIVE POSIX paths keyed as
    # ``f"{root.name}/{py_file.relative_to(root).as_posix()}"`` — the exact
    # same shape produced by `_containment_allowlist_key` below and emitted
    # in offender strings by `_find_*_containment_offenders`. This means a
    # real containment failure's offender string can be copy-pasted verbatim
    # into this allowlist without re-keying.
    #
    # Basename-only keys (the previous shape) let a file at
    # ``scripts/ollama_gemma_provider.py`` or
    # ``app/features/extraction/intelligence/subdir/ollama_gemma_provider.py``
    # inherit the real file's allowlist entry and evade containment (PR #464
    # Copilot re-review finding). Path-based keys pin each allowlist entry to
    # the single file that's authorised to reach the contained package.
    # Docling containment expanded from a single file to the set of
    # parsing/* files introduced by the issue #159 refactor.
    "docling": frozenset(
        {
            "app/features/extraction/parsing/docling_document_parser.py",
            "app/features/extraction/parsing/_real_docling_converter_adapter.py",
            "app/features/extraction/parsing/_real_docling_document_adapter.py",
        },
    ),
    "pymupdf": frozenset(
        {
            "app/features/extraction/annotation/pdf_annotator.py",
            "app/features/extraction/parsing/docling_document_parser.py",
        },
    ),
    "fitz": frozenset(
        {
            "app/features/extraction/annotation/pdf_annotator.py",
            "app/features/extraction/parsing/docling_document_parser.py",
        },
    ),
    "langextract": frozenset(
        {
            "app/features/extraction/extraction/extraction_engine.py",
            "app/features/extraction/extraction/_validating_langextract_adapter.py",
            "app/features/extraction/intelligence/ollama_gemma_provider.py",
        },
    ),
    # `scripts/benchmark.py` ships the local-latency benchmark CLI and uses
    # `httpx` as a plain HTTP client to talk to the running FastAPI service —
    # NOT as an Ollama client. It is listed here because the containment scan
    # covers both `_APP_ROOT` and `_SCRIPTS_ROOT` (issue #327); restricting
    # the allowlist to the two Ollama-client files inside the feature would
    # make `scripts/benchmark.py` a false-positive offender.
    "httpx": frozenset(
        {
            "app/features/extraction/intelligence/ollama_gemma_provider.py",
            "app/features/extraction/intelligence/ollama_health_probe.py",
            "scripts/benchmark.py",
        },
    ),
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


def _iter_module_scope_imports(tree: ast.Module) -> Iterator[ast.Import | ast.ImportFrom]:
    """Yield every Import/ImportFrom that binds a name at module scope.

    Descends into compound statements (If/Try/TryStar/With/For/While/Match)
    whose bodies execute at module scope, but NOT into FunctionDef/
    AsyncFunctionDef/ClassDef, where imports bind locally and do not
    affect module-scope name resolution.

    Closes the PR #310 re-review gap where ``try: import importlib.util as u``
    at module scope created a valid binding that the previous top-level-only
    walker missed. The compound-statement bodies named above do not create
    their own lexical scope in Python — names bound inside them are visible
    at module scope — so module-scope alias tracking must descend through
    them. Function, async-function, and class bodies, in contrast, DO create
    their own scopes and are correctly skipped to avoid treating a local
    import as a module-scope binding.

    Python 3.11 added ``try*`` (``ast.TryStar``) and 3.10 added
    ``match``/``case`` (``ast.Match``). Both run their bodies at module
    scope when used at the top level, so they must be descended into as
    well — missing either leaves a bypass for the containment gate (a
    module-scope ``match: case: import importlib.util as u`` would not
    be seen). ``ast.TryStar`` is probed via ``getattr`` because older
    Pythons lack the attribute; the fallback is to skip the check cleanly
    rather than hard-import a symbol that may not exist.
    """
    try_star_type: type[ast.AST] | None = getattr(ast, "TryStar", None)
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop(0)
        if isinstance(node, ast.Import | ast.ImportFrom):
            yield node
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            # New lexical scope; don't descend.
            continue
        if isinstance(node, ast.If):
            stack[:0] = list(node.body) + list(node.orelse)
        elif isinstance(node, ast.Try):
            handlers_body: list[ast.stmt] = [s for h in node.handlers for s in h.body]
            stack[:0] = list(node.body) + handlers_body + list(node.orelse) + list(node.finalbody)
        elif try_star_type is not None and isinstance(node, try_star_type):
            # ``ast.TryStar`` (Python 3.11+) mirrors ``ast.Try`` — same
            # body/handlers/orelse/finalbody shape, so descend identically.
            handlers_body = [s for h in node.handlers for s in h.body]  # type: ignore[attr-defined]  # TryStar exposes handlers like Try
            stack[:0] = (
                list(node.body)  # type: ignore[attr-defined]
                + handlers_body
                + list(node.orelse)  # type: ignore[attr-defined]
                + list(node.finalbody)  # type: ignore[attr-defined]
            )
        elif isinstance(node, ast.With | ast.AsyncWith):
            stack[:0] = list(node.body)
        elif isinstance(node, ast.For | ast.AsyncFor | ast.While):
            stack[:0] = list(node.body) + list(node.orelse)
        elif isinstance(node, ast.Match):
            # match/case bodies (Python 3.10+) execute at module scope when
            # used at the top level. Descend into every case's body.
            cases_body: list[ast.stmt] = [s for c in node.cases for s in c.body]
            stack[:0] = cases_body
        # else: expression statement, assignment, etc. — no nested scope to
        # descend into.


def _importlib_aliases_from_module(tree: ast.Module) -> frozenset[str]:
    """Return local names bound to the ``importlib`` package at module scope.

    Scans module-scope imports (including those nested under top-level
    ``try`` / ``if`` / ``with`` / ``for`` / ``while`` bodies) — but NOT
    inside function or class bodies — via ``_iter_module_scope_imports``.
    Picks up three shapes:

    * ``import importlib`` → binds ``importlib``
    * ``import importlib as il`` → binds ``il``
    * ``import importlib.util`` → binds ``importlib`` (Python's standard
      package-import semantics — the root package name is always bound)

    PR #310 review follow-up: tracking aliased bindings is load-bearing
    because ``import importlib as il; il.util.find_spec("pkg")`` was a
    silent bypass of the containment gate under the literal-match-only
    logic. ``import importlib.util as u`` is handled separately in
    `_importlib_util_aliases_from_module` below because it binds ``u``
    to the SUBMODULE (``u.find_spec(...)``), not to ``importlib``
    (``u.util.find_spec(...)``). Widened (PR #310 re-review) to see
    imports nested under top-level compound statements, because
    ``try: import importlib as il`` at module scope still binds ``il``
    globally.
    """
    aliases: set[str] = set()
    for node in _iter_module_scope_imports(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            if alias.name == "importlib":
                aliases.add(alias.asname if alias.asname is not None else "importlib")
            elif alias.name.startswith("importlib.") and alias.asname is None:
                # `import importlib.util` (no alias) binds `importlib` at
                # module scope. `import importlib.util as u` binds `u`
                # to the submodule, not to `importlib`, so skip when an
                # asname is present — that case is handled separately
                # by `_importlib_util_aliases_from_module`.
                aliases.add("importlib")
    return frozenset(aliases)


def _importlib_util_aliases_from_module(tree: ast.Module) -> frozenset[str]:
    """Return local names bound DIRECTLY to the ``importlib.util`` submodule.

    Scans module-scope imports (including those nested under top-level
    ``try`` / ``if`` / ``with`` / ``for`` / ``while`` bodies) — but NOT
    inside function or class bodies — via ``_iter_module_scope_imports``
    for ``import importlib.util as <alias>`` or
    ``from importlib import util as <alias>``. The resulting aliases are
    bound to the ``util`` submodule, so ``<alias>.find_spec(...)`` is the
    availability probe — a one-shorter attribute chain than the
    ``<importlib-alias>.util.find_spec(...)`` form handled by
    `_is_importlib_util_find_spec_attribute`.

    PR #310 review follow-up: ``import importlib.util as u; u.find_spec(
    "pkg")`` was still a silent bypass after the first alias-tracking
    pass, because the submodule alias never bound ``importlib`` and the
    call shape is ``<name>.find_spec(...)``, not the three-segment
    chain. This helper closes that hole. Widened (PR #310 re-review)
    to see imports nested under top-level compound statements, because
    ``try: import importlib.util as u`` at module scope still binds ``u``
    globally.
    """
    aliases: set[str] = set()
    for node in _iter_module_scope_imports(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib.util" and alias.asname is not None:
                    aliases.add(alias.asname)
        else:
            # `_iter_module_scope_imports` yields only Import | ImportFrom,
            # so the non-Import branch is always ImportFrom.
            if node.module != "importlib" or node.level != 0:
                continue
            for alias in node.names:
                if alias.name == "util":
                    aliases.add(alias.asname if alias.asname is not None else "util")
    return frozenset(aliases)


def _find_spec_bindings_from_importlib_util(tree: ast.Module) -> frozenset[str]:
    """Return local names bound to ``importlib.util.find_spec`` at module scope.

    Scans module-scope imports (including those nested under top-level
    ``try`` / ``if`` / ``with`` / ``for`` / ``while`` bodies) — but NOT
    inside function or class bodies — via ``_iter_module_scope_imports``.
    An ``ImportFrom`` inside a function body does not create a module-level
    name binding, so walking the full tree (``ast.walk``) would falsely
    flag calls to the module-scope ``find_spec`` symbol when the
    import lives in a nested function/class. Compound statements like
    ``try`` and ``if`` at the top level, in contrast, DO bind names at
    module scope and must be descended into.

    Includes aliased forms so ``from importlib.util import find_spec as fs``
    records ``fs`` as a detected target (PR #310 review follow-up). Without
    this, ``fs("langextract")`` was a silent bypass of the gate.

    PR #310 re-review follow-up: widened from ``tree.body``-only to the
    compound-statement-descending walker so that
    ``try: from importlib.util import find_spec as fs`` at module scope
    is detected — a previously silent bypass.

    Limitation: if a source imports ``find_spec`` and later rebinds the
    local name (``fs = something_else``), the walker still counts calls
    to that imported name as containment targets. Local reassignment of
    an import-bound name is rare enough that this remains an accepted
    first-pass narrowing; documenting it here keeps the trade-off
    visible for future readers.
    """
    bound_names: set[str] = set()
    for node in _iter_module_scope_imports(tree):
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

    The literal name ``importlib`` is also always accepted, even when
    the module-scope scan did not find an ``import importlib`` statement
    at module scope. A function-local ``import importlib.util`` inside
    an ``async def`` or ``def`` body binds ``importlib`` only within
    that function, and the subsequent ``importlib.util.find_spec(...)``
    call site would be missed by a module-scope-only alias set — a
    straightforward bypass of the containment gate. Accepting the
    literal root mirrors how ``__import__`` and ``import_module`` are
    treated (always recognised regardless of surrounding import
    context) and still avoids false positives because the matcher
    requires the full three-segment ``<name>.util.find_spec`` chain.
    """
    if not isinstance(func, ast.Attribute) or func.attr != "find_spec":
        return False
    util = func.value
    if not isinstance(util, ast.Attribute) or util.attr != "util":
        return False
    importlib_name = util.value
    if not isinstance(importlib_name, ast.Name):
        return False
    return importlib_name.id == "importlib" or importlib_name.id in importlib_aliases


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

    * For the attribute form, the canonical chain
      ``importlib.util.find_spec`` and equivalent alias-based chains such
      as ``il.util.find_spec(...)``, ``util.find_spec(...)``, or
      ``u.find_spec(...)`` are matched when those names are bound from
      ``importlib`` / ``importlib.util`` at module scope (see
      ``_importlib_aliases_from_module`` and
      ``_importlib_util_aliases_from_module``). The literal root name
      ``importlib`` is also always accepted so that a function-local
      ``import importlib.util`` followed by
      ``importlib.util.find_spec(...)`` is not a bypass.
      ``obj.find_spec(...)`` on any unrelated object is ignored.
    * For the bare-name form, the source must also contain a module-scope
      ``from importlib.util import find_spec [as <alias>]`` binding for
      the call to count as a containment target. ``<alias>(...)`` is
      detected when aliased. A module that defines a local ``find_spec``
      function (or imports one from a different package) is not flagged,
      and a nested ``ImportFrom`` inside a ``def``/``class`` body is
      intentionally NOT treated as module-scope binding.

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
    importlib_util_aliases = _importlib_util_aliases_from_module(tree)
    find_spec_local_bindings = _find_spec_bindings_from_importlib_util(tree)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not _call_is_dynamic_import(
            func,
            importlib_aliases=importlib_aliases,
            importlib_util_aliases=importlib_util_aliases,
            find_spec_local_bindings=find_spec_local_bindings,
        ):
            continue
        # Prefer the positional arg (canonical form); fall back to the
        # ``name=`` keyword form so calls like
        # ``importlib.util.find_spec(name="langextract")`` or
        # ``importlib.import_module(name="app.features.billing.foo")``
        # do not silently evade the gate (PR #310 re-review follow-up).
        arg: ast.expr | None = None
        if node.args:
            arg = node.args[0]
        else:
            for kw in node.keywords:
                if kw.arg == "name":
                    arg = kw.value
                    break
        if arg is None:
            continue
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            targets.add(arg.value)
    return targets


def _call_is_dynamic_import(
    func: ast.expr,
    *,
    importlib_aliases: frozenset[str],
    importlib_util_aliases: frozenset[str],
    find_spec_local_bindings: frozenset[str],
) -> bool:
    """Return True iff ``func`` names one of the tracked dynamic-import callables.

    Handles the callable shapes covered by the walker:

    * ``<anything>.import_module(...)`` (attribute call; the ``importlib``
      root is not verified because the canonical pattern
      ``importlib.import_module`` is by far the dominant use and a false
      positive is harmless for containment purposes — any string-literal
      argument is still gated by the per-package allowlist),
    * the 3-segment chain ``<alias>.util.find_spec(...)`` where
      ``<alias>`` is any local name bound to the ``importlib`` package,
    * the 2-segment chain ``<util-alias>.find_spec(...)`` where
      ``<util-alias>`` is any local name bound directly to the
      ``importlib.util`` submodule (PR #310 review follow-up:
      ``import importlib.util as u; u.find_spec(...)`` and
      ``from importlib import util; util.find_spec(...)`` are now
      recognised),
    * bare ``import_module(...)`` or ``__import__(...)`` calls, and
    * bare ``<name>(...)`` calls where ``<name>`` is any module-scope
      binding of ``importlib.util.find_spec`` — covers the canonical
      ``from importlib.util import find_spec`` AND the aliased
      ``from importlib.util import find_spec as fs`` form.
    """
    if isinstance(func, ast.Attribute):
        if func.attr == "import_module":
            return True
        if _is_importlib_util_find_spec_attribute(func, importlib_aliases=importlib_aliases):
            return True
        if func.attr == "find_spec" and isinstance(func.value, ast.Name):
            return func.value.id in importlib_util_aliases
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


def _iter_containment_py_files(roots: tuple[Path, ...]) -> Iterator[tuple[Path, Path]]:
    """Yield (root, py_file) pairs for every .py file under each root.

    Roots that do not exist on disk are silently skipped so this helper can
    be called with both the production `_CONTAINMENT_ROOTS` tuple AND a
    synthetic tuple assembled from `tmp_path` (where only one of the roots
    has been materialised) without branching at every call site.
    """
    for root in roots:
        if not root.is_dir():
            continue
        for py_file in root.rglob("*.py"):
            yield root, py_file


def _containment_allowlist_key(root: Path, py_file: Path) -> str:
    """Return the canonical allowlist key for a containment-scan py file.

    Single source of truth for the string shape used to both

    * key ``_CONTAINED_PACKAGES`` entries, and
    * emit offender paths from ``_find_*_containment_offenders``.

    Sharing one helper is load-bearing: the "allowlist key format mirrors
    offender string format" invariant means a real failure's offender string
    can be copy-pasted verbatim into the corresponding ``_CONTAINED_PACKAGES``
    entry. A drift between the two shapes would silently force maintainers
    to reconstruct the key, exactly the fragility the basename-keyed scheme
    produced (PR #464 Copilot re-review finding).

    The shape is ``f"{root.name}/{py_file.relative_to(root).as_posix()}"``:
    the root's basename (e.g. ``app`` or ``scripts``) plus the file's
    relative POSIX path from that root. Basename-only keys let a namesake
    under a different root — or a subdirectory — inherit the real file's
    allowlist entry and bypass containment.
    """
    return f"{root.name}/{py_file.relative_to(root).as_posix()}"


def _find_dynamic_import_containment_offenders(
    roots: tuple[Path, ...],
    *,
    package: str,
    allowed_files: frozenset[str],
) -> list[str]:
    """Return relative paths (per-root) of files that dynamically import `package`.

    Factored out so the production parametrized test and the synthetic
    regression test (``test_containment_scan_covers_scripts_directory``)
    invoke the SAME predicate. Coupling both tests to this helper means a
    regression that narrows the scan back to `_APP_ROOT` alone fails the
    synthetic tmp_path test first, with a direct pointer back to issue #327.

    The returned strings embed the root's basename (e.g. `app/foo.py` vs
    `scripts/foo.py`) so a real failure message tells the reader WHICH
    containment root the offender lives under, not just the filename.

    Allowlist lookup keys on the FULL relative POSIX path from the root
    (via ``_containment_allowlist_key``), not the basename. PR #464
    Copilot re-review: a basename-keyed allowlist let a file at
    ``scripts/ollama_gemma_provider.py`` inherit the real file's allowlist
    entry and evade containment; relative-path keying pins each entry to
    exactly one authorised file.
    """
    offenders: list[str] = []
    for root, py_file in _iter_containment_py_files(roots):
        if _containment_allowlist_key(root, py_file) in allowed_files:
            continue
        targets = _collect_dynamic_import_targets(py_file.read_text(encoding="utf-8"))
        if any(_target_matches_package(target, package) for target in targets):
            offenders.append(_containment_allowlist_key(root, py_file))
    return offenders


def _find_static_import_containment_offenders(
    roots: tuple[Path, ...],
    *,
    package: str,
    allowed_files: frozenset[str],
) -> list[str]:
    """Return relative paths (per-root) of files that statically import `package`.

    Static counterpart to `_find_dynamic_import_containment_offenders`. Same
    rationale for the helper: the synthetic regression test and the real
    parametrized walk must share one predicate so a scoped-to-app regression
    fails the synthetic test directly.

    Allowlist lookup keys on the FULL relative POSIX path from the root
    (via ``_containment_allowlist_key``), not the basename. See the
    companion docstring for the PR #464 Copilot re-review rationale.
    """
    offenders: list[str] = []
    for root, py_file in _iter_containment_py_files(roots):
        if _containment_allowlist_key(root, py_file) in allowed_files:
            continue
        roots_imported = _collect_static_root_imports(py_file.read_text(encoding="utf-8"))
        if package in roots_imported:
            offenders.append(_containment_allowlist_key(root, py_file))
    return offenders


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
    Scoped to `_CONTAINMENT_ROOTS` (both `app/` and `scripts/`) rather than
    just `app/`. The import-linter C3-C6 contracts set
    `source_modules = app`, which leaves `scripts/benchmark.py` and
    `scripts/validate_skills.py` free to `import docling` / `import httpx`
    without tripping any gate (issue #327). Walking both containment roots
    here closes that stealth-escape path.
    """
    offenders = _find_dynamic_import_containment_offenders(
        _CONTAINMENT_ROOTS,
        package=package,
        allowed_files=allowed_files,
    )

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
    It adds coverage for the full backend tree (both `_APP_ROOT` and
    `_SCRIPTS_ROOT`) and catches edge cases where a static import sneaks
    through between lint-imports runs. The `scripts/` coverage is the
    fix for issue #327 — the import-linter contracts use
    `source_modules = app` and therefore cannot see a `scripts/` file
    that statically imports a contained package.
    """
    offenders = _find_static_import_containment_offenders(
        _CONTAINMENT_ROOTS,
        package=package,
        allowed_files=allowed_files,
    )

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


def test_collect_dynamic_import_targets_records_importlib_util_alias_find_spec() -> None:
    """``import importlib.util as u; u.find_spec(...)`` must be detected.

    PR #310 review follow-up: the first-pass alias-tracking only handled
    root ``importlib`` aliases (matching the 3-segment
    ``<alias>.util.find_spec`` chain). Binding the SUBMODULE directly
    (``import importlib.util as u``) produces a shorter 2-segment call
    (``u.find_spec(...)``) that the first-pass logic missed. The helper
    `_importlib_util_aliases_from_module` collects those submodule
    aliases and the matcher now checks them against 2-segment attribute
    calls.
    """
    source = 'import importlib.util as u\nu.find_spec("langextract")\n'
    targets = _collect_dynamic_import_targets(source)
    assert "langextract" in targets


def test_collect_dynamic_import_targets_records_from_importlib_import_util_find_spec() -> None:
    """``from importlib import util; util.find_spec(...)`` must be detected.

    Same 2-segment shape as the ``import importlib.util as u`` case,
    but reached via ``ImportFrom``. The helper collects both. The
    canonical local name ``util`` is covered; aliased forms
    (``from importlib import util as u``) are also picked up.
    """
    source = 'from importlib import util\nutil.find_spec("langextract")\n'
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


def test_collect_dynamic_import_targets_records_find_spec_under_top_level_try() -> None:
    """A ``try:`` at module scope is still module scope — imports inside bind globally.

    PR #310 re-review follow-up: the previous alias-tracking helpers scanned
    only direct children of ``tree.body``, which missed
    ``try: import importlib.util as u`` even though that ``try`` executes at
    module scope and therefore binds ``u`` globally. The fix widens the scan
    via ``_iter_module_scope_imports`` to descend into compound statements
    (If/Try/With/For/While) whose bodies bind at module scope, while
    correctly NOT descending into FunctionDef/AsyncFunctionDef/ClassDef
    (which DO create their own scopes).
    """
    source = (
        "try:\n"
        "    import importlib.util as u\n"
        "except ImportError:\n"
        "    u = None\n"
        'u.find_spec("langextract")\n'
    )
    assert "langextract" in _collect_dynamic_import_targets(source)


def test_collect_dynamic_import_targets_records_find_spec_from_under_top_level_try() -> None:
    """``try: from importlib.util import find_spec as fs`` at module scope still binds ``fs``.

    Same PR #310 re-review follow-up as the previous test, but for the
    ``ImportFrom`` form: ``_find_spec_bindings_from_importlib_util`` used
    to iterate only ``tree.body`` direct children, missing imports nested
    under a top-level ``try``/``if``/``with``/``for``/``while`` body. The
    widened walker handles both.
    """
    source = (
        "try:\n"
        "    from importlib.util import find_spec as fs\n"
        "except ImportError:\n"
        "    fs = None\n"
        'fs("langextract")\n'
    )
    assert "langextract" in _collect_dynamic_import_targets(source)


def test_collect_dynamic_import_targets_records_find_spec_under_top_level_match() -> None:
    """Module-scope ``match``/``case`` bodies bind at module scope too.

    PR #310 re-review follow-up: Python 3.10 added structural pattern
    matching; a top-level ``match`` executes its chosen ``case`` body at
    module scope, so imports inside bind globally. Missing this path
    let ``match sys.platform: case "linux": import importlib.util as u``
    at module scope bypass alias tracking.
    """
    source = (
        "import sys\n"
        "match sys.platform:\n"
        '    case "linux":\n'
        "        import importlib.util as u\n"
        "    case _:\n"
        "        u = None\n"
        'u.find_spec("langextract")\n'
    )
    assert "langextract" in _collect_dynamic_import_targets(source)


def test_collect_dynamic_import_targets_records_function_local_importlib_util() -> None:
    """A function-local ``import importlib.util`` still lets the file's
    ``importlib.util.find_spec(...)`` calls be detected.

    PR #310 re-review follow-up: previously the attribute-chain matcher
    required ``importlib`` to be bound at module scope. A file that did
    its ``import importlib.util`` inside a function (with a bare
    ``importlib.util.find_spec(...)`` call elsewhere) would silently slip
    the gate. The literal root name ``importlib`` is now always accepted
    for the 3-segment chain; alias forms still need module-scope binding.
    """
    source = (
        "def maybe_probe() -> None:\n"
        "    import importlib.util\n"
        '    importlib.util.find_spec("langextract")\n'
    )
    assert "langextract" in _collect_dynamic_import_targets(source)


def test_collect_dynamic_import_targets_records_keyword_name_argument() -> None:
    """``importlib.util.find_spec(name="pkg")`` and ``importlib.import_module(name="pkg")`` must be detected.

    PR #310 re-review follow-up: the previous collector only inspected
    ``node.args[0]``, so callers passing the module name as a keyword
    argument (the canonical parameter name is ``name`` for both
    ``importlib.import_module`` and ``importlib.util.find_spec``) bypassed
    the gate. The fix falls back to ``node.keywords`` when ``node.args``
    is empty and picks up a string-constant bound to ``name=``.
    """
    source = (
        "import importlib\n"
        "import importlib.util\n"
        'importlib.util.find_spec(name="langextract")\n'
        'importlib.import_module(name="docling.datamodel")\n'
    )
    targets = _collect_dynamic_import_targets(source)
    assert "langextract" in targets
    assert "docling.datamodel" in targets


def test_collect_dynamic_import_targets_records_aliased_find_spec_docling_issue_401() -> None:
    """Issue #401: ``from importlib.util import find_spec as fs; fs("docling")`` must be flagged.

    The issue audit flagged a stale detection branch that matched
    ``isinstance(func, ast.Attribute) and func.attr in {"import_module", "find_spec"}``
    and would therefore miss the bare-``Name``-call shape produced by
    ``from importlib.util import find_spec as fs``, whose call site
    ``fs("docling")`` presents as ``ast.Name(id="fs")`` with no attribute
    chain. The scan had to track the ``asname`` binding (``fs``) rather than
    only the imported symbol name (``find_spec``) or the alias would let a
    containment-gate-bypassing availability probe for Docling slip through
    both import-linter (the call is an expression, not an ``import``
    statement) and the AST scan.

    Overlaps in intent with
    ``test_collect_dynamic_import_targets_records_aliased_from_import_find_spec``
    (which uses ``"langextract"``), but pins the EXACT example string from
    the issue body (``"docling"``) so a regression against the specific
    scenario the audit flagged fails loudly with a direct reference back
    to issue #401.
    """
    source = 'from importlib.util import find_spec as fs; fs("docling")\n'
    targets = _collect_dynamic_import_targets(source)
    assert "docling" in targets, (
        f"issue #401 regression: aliased `fs('docling')` not detected; targets={targets!r}"
    )


def test_containment_roots_include_scripts_directory() -> None:
    """Issue #327: the containment-scan root tuple must cover `scripts/`.

    The audit found that `_APP_ROOT`-only scans let
    `scripts/benchmark.py` and `scripts/validate_skills.py` freely reach
    Docling / PyMuPDF / LangExtract / Ollama-httpx without tripping any
    containment gate — both the AST scan here and the import-linter C3-C6
    contracts (which pin `source_modules = app`) missed that tree. The
    fix widens the AST scan's root tuple from `(_APP_ROOT,)` to
    `(_APP_ROOT, _SCRIPTS_ROOT)`; this test pins the invariant so a
    future refactor that drops `_SCRIPTS_ROOT` from `_CONTAINMENT_ROOTS`
    fails here before any real violation can land on main.
    """
    assert _SCRIPTS_ROOT in _CONTAINMENT_ROOTS, (
        f"issue #327 regression: `_SCRIPTS_ROOT` must be in `_CONTAINMENT_ROOTS`; "
        f"got {_CONTAINMENT_ROOTS!r}"
    )
    assert _APP_ROOT in _CONTAINMENT_ROOTS, (
        f"`_APP_ROOT` must remain in `_CONTAINMENT_ROOTS`; got {_CONTAINMENT_ROOTS!r}"
    )


def test_containment_scan_covers_scripts_directory(tmp_path: Path) -> None:
    """Issue #327: planted Docling import under `scripts/` must be flagged.

    Synthesizes a two-root backend-style layout under `tmp_path`:
    ``tmp_path/app/core/config.py`` (clean) and two rogue files under
    ``tmp_path/scripts/``: ``rogue_static.py`` (plants
    ``import docling``) and ``rogue_dynamic.py`` (plants
    ``import_module("langextract")`` to exercise the dynamic-import
    branch too). The predicate under test — the same helper that the
    production parametrized walk uses — MUST flag both rogue files
    and leave ``app/core/config.py`` alone. Before the fix the scan
    only walked `app/`, so the planted rogues sailed through; the
    assertions here are what go red against that code.

    Keying the test on ``tmp_path`` (not on the real `scripts/`
    tree) keeps it independent of whatever real scripts ship today
    — the invariant being pinned is "the scan covers `scripts/` as
    a tree", not "no real script imports Docling today". The real-tree
    coverage is enforced by the production parametrized test above;
    the synthetic harness here guarantees that if someone narrows
    the production walk back to one root, this test fails first
    with a pointer back to issue #327.

    Also flips to the negative: a synthetic tuple of ``(tmp_path/app,)``
    alone — i.e. deliberately excluding the `scripts/` root — MUST
    NOT flag the rogue. That direction rules out a trivially-passing
    helper that flags everything regardless of its `roots` argument.
    """
    app_root = tmp_path / "app" / "core"
    scripts_root = tmp_path / "scripts"
    app_root.mkdir(parents=True)
    scripts_root.mkdir(parents=True)
    (app_root / "config.py").write_text("BACKEND_PORT = 8000\n", encoding="utf-8")
    (scripts_root / "rogue_static.py").write_text("import docling\n", encoding="utf-8")
    (scripts_root / "rogue_dynamic.py").write_text(
        'import importlib\nimportlib.import_module("langextract")\n',
        encoding="utf-8",
    )

    roots_with_scripts = (tmp_path / "app", scripts_root)
    roots_without_scripts = (tmp_path / "app",)

    static_offenders_with_scripts = _find_static_import_containment_offenders(
        roots_with_scripts,
        package="docling",
        allowed_files=frozenset(),
    )
    dynamic_offenders_with_scripts = _find_dynamic_import_containment_offenders(
        roots_with_scripts,
        package="langextract",
        allowed_files=frozenset(),
    )

    assert "scripts/rogue_static.py" in static_offenders_with_scripts, (
        "issue #327 regression: static scan did not flag `scripts/rogue_static.py`; "
        f"offenders={static_offenders_with_scripts!r}"
    )
    assert "scripts/rogue_dynamic.py" in dynamic_offenders_with_scripts, (
        "issue #327 regression: dynamic scan did not flag `scripts/rogue_dynamic.py`; "
        f"offenders={dynamic_offenders_with_scripts!r}"
    )

    # Negative direction: an `_APP_ROOT`-only tuple (the pre-fix scope) must
    # NOT flag a `scripts/` rogue, because its walk never reaches that tree.
    # This is the stealth-escape path the issue describes: restricting the
    # scan to `app/` silently disables enforcement against `scripts/`.
    static_offenders_app_only = _find_static_import_containment_offenders(
        roots_without_scripts,
        package="docling",
        allowed_files=frozenset(),
    )
    dynamic_offenders_app_only = _find_dynamic_import_containment_offenders(
        roots_without_scripts,
        package="langextract",
        allowed_files=frozenset(),
    )
    assert all(not o.startswith("scripts/") for o in static_offenders_app_only), (
        "sanity: `app/`-only scan must not reach `scripts/` — if it does, the "
        "helpers treat the `roots` argument as advisory and the positive "
        "assertions above would pass vacuously"
    )
    assert all(not o.startswith("scripts/") for o in dynamic_offenders_app_only), (
        "sanity: `app/`-only dynamic scan must not reach `scripts/`"
    )


def test_containment_scan_respects_allowlist_under_scripts_root(tmp_path: Path) -> None:
    """Issue #327: an allowlisted file under `scripts/` must not be flagged.

    Companion to ``test_containment_scan_covers_scripts_directory`` — proves
    the `scripts/` extension does NOT break the allowlist semantics. Plants
    a file named `benchmark.py` under a synthetic `scripts/` root that
    imports `httpx` (the real allowlist entry for `scripts/benchmark.py`
    mirrors this), and asserts the containment helper does NOT flag it.
    Without this test, a regression that scanned `scripts/` but ignored
    the allowlist would pass the positive "flag the rogue" test silently
    while false-positiving on every legitimate use.

    The allowlist is keyed on the FULL relative POSIX path
    (``scripts/benchmark.py``), not the basename — see the PR #464
    Copilot re-review rationale captured in ``_containment_allowlist_key``.
    """
    scripts_root = tmp_path / "scripts"
    scripts_root.mkdir(parents=True)
    (scripts_root / "benchmark.py").write_text("import httpx\n", encoding="utf-8")
    (scripts_root / "rogue_benchmark_namesake.py").write_text(
        "import httpx\n",
        encoding="utf-8",
    )

    offenders = _find_static_import_containment_offenders(
        (scripts_root,),
        package="httpx",
        allowed_files=frozenset({"scripts/benchmark.py"}),
    )

    assert "scripts/benchmark.py" not in offenders, (
        f"allowlist regression: `benchmark.py` under scripts/ wrongly flagged: {offenders!r}"
    )
    assert "scripts/rogue_benchmark_namesake.py" in offenders, (
        f"non-allowlisted sibling must still be flagged: {offenders!r}"
    )


def test_containment_scan_rejects_basename_namesake_in_wrong_root(tmp_path: Path) -> None:
    """Issue #327 re-review: a basename namesake under the WRONG root must still be flagged.

    Before this fix the containment scan keyed its allowlist on file
    basename, so a file at ``scripts/ollama_gemma_provider.py`` inherited
    the real file's allowlist entry (the production
    ``app/features/extraction/intelligence/ollama_gemma_provider.py``)
    and evaded containment despite living in an entirely different root.

    Plants ``scripts/ollama_gemma_provider.py`` that dynamically imports
    ``httpx`` and a clean
    ``app/features/extraction/intelligence/ollama_gemma_provider.py``
    under ``tmp_path``, then allowlists only the real file's relative path
    and asserts the namesake under ``scripts/`` is flagged by the dynamic
    scan. Red-fails under the basename-keyed scheme because both files
    share ``py_file.name``. The static-branch counterpart is covered by
    ``test_containment_scan_rejects_basename_namesake_in_subdir``.
    """
    app_root = tmp_path / "app"
    scripts_root = tmp_path / "scripts"
    intelligence_dir = app_root / "features" / "extraction" / "intelligence"
    intelligence_dir.mkdir(parents=True)
    scripts_root.mkdir(parents=True)
    (intelligence_dir / "ollama_gemma_provider.py").write_text(
        "import httpx\n",
        encoding="utf-8",
    )
    (scripts_root / "ollama_gemma_provider.py").write_text(
        'import importlib\nimportlib.import_module("httpx")\n',
        encoding="utf-8",
    )

    allowed = frozenset(
        {"app/features/extraction/intelligence/ollama_gemma_provider.py"},
    )
    roots = (app_root, scripts_root)

    dynamic_offenders = _find_dynamic_import_containment_offenders(
        roots,
        package="httpx",
        allowed_files=allowed,
    )

    assert "scripts/ollama_gemma_provider.py" in dynamic_offenders, (
        "basename-bypass regression: a namesake under `scripts/` inherited the "
        f"real file's allowlist entry; offenders={dynamic_offenders!r}"
    )
    assert (
        "app/features/extraction/intelligence/ollama_gemma_provider.py" not in dynamic_offenders
    ), (
        "real allowlisted file wrongly flagged — allowlist key must match the "
        f"full relative path: offenders={dynamic_offenders!r}"
    )


def test_containment_scan_rejects_basename_namesake_in_subdir(tmp_path: Path) -> None:
    """Issue #327 re-review: a basename namesake in a SUBDIR under the SAME root must still be flagged.

    Before this fix the containment scan keyed its allowlist on file
    basename, so a file at
    ``app/features/extraction/intelligence/sub/ollama_gemma_provider.py``
    inherited the real file's allowlist entry and could run arbitrary
    ``httpx`` calls without tripping the gate. Both files plant the same
    static ``import httpx`` so a regression appears in the static-offenders
    list; the dynamic counterpart is covered by
    ``test_containment_scan_rejects_basename_namesake_in_wrong_root``.
    """
    app_root = tmp_path / "app"
    intelligence_dir = app_root / "features" / "extraction" / "intelligence"
    sub_dir = intelligence_dir / "sub"
    sub_dir.mkdir(parents=True)
    (intelligence_dir / "ollama_gemma_provider.py").write_text(
        "import httpx\n",
        encoding="utf-8",
    )
    (sub_dir / "ollama_gemma_provider.py").write_text(
        "import httpx\n",
        encoding="utf-8",
    )

    allowed = frozenset(
        {"app/features/extraction/intelligence/ollama_gemma_provider.py"},
    )
    roots = (app_root,)

    static_offenders = _find_static_import_containment_offenders(
        roots,
        package="httpx",
        allowed_files=allowed,
    )
    dynamic_offenders = _find_dynamic_import_containment_offenders(
        roots,
        package="httpx",
        allowed_files=allowed,
    )

    subdir_rel = "app/features/extraction/intelligence/sub/ollama_gemma_provider.py"
    assert subdir_rel in static_offenders, (
        "basename-bypass regression: namesake in a subdir inherited the real "
        f"file's allowlist entry; static offenders={static_offenders!r}"
    )
    assert (
        "app/features/extraction/intelligence/ollama_gemma_provider.py" not in static_offenders
    ), (
        "real allowlisted file wrongly flagged — allowlist key must match the "
        f"full relative path: static offenders={static_offenders!r}"
    )
    # Sanity: neither file uses dynamic imports, so the dynamic walk must
    # stay empty. Catches a regression that would over-flag by misreading
    # static ``import httpx`` as a dynamic target.
    assert not dynamic_offenders, (
        f"dynamic walk must stay empty for static-only sources: {dynamic_offenders!r}"
    )


def test_containment_allowlist_key_format_mirrors_offender_string() -> None:
    """Pin the invariant: the allowlist-key helper produces the SAME string shape as the offender list.

    The containment helpers report offenders as
    ``f"{root.name}/{py_file.relative_to(root).as_posix()}"``. The
    allowlist key must use the identical shape so that a real failure's
    offender string can be copy-pasted verbatim into the corresponding
    ``_CONTAINED_PACKAGES`` entry without translation. A mismatch
    (e.g. one side omitting ``root.name``) would force maintainers to
    mentally reconstruct the key, which is exactly the fragility the
    basename-keyed scheme produced.

    Red-fails if ``_containment_allowlist_key`` does not exist yet, or if
    its output diverges from the offender string the production helpers
    would emit.
    """
    root = Path("/any/prefix/app")
    py_file = root / "features" / "extraction" / "intelligence" / "ollama_gemma_provider.py"

    key = _containment_allowlist_key(root, py_file)
    offender = f"{root.name}/{py_file.relative_to(root).as_posix()}"

    assert key == offender, (
        "allowlist-key format must match offender-string format verbatim; "
        f"got key={key!r} vs offender={offender!r}"
    )
    assert key == "app/features/extraction/intelligence/ollama_gemma_provider.py", (
        f"unexpected key shape: {key!r}"
    )


def test_iter_containment_py_files_skips_nonexistent_root(tmp_path: Path) -> None:
    """`_iter_containment_py_files` must silently skip roots that don't exist.

    Belt-and-braces for environments where, for whatever reason,
    `apps/backend/scripts/` does not exist on disk (e.g. a slimmed-down
    consumer fork or an in-progress refactor). Without this, the
    production parametrized tests would error with
    ``FileNotFoundError`` the moment either root is absent, masking
    the underlying containment signal behind an infrastructure failure.
    The synthetic regression test above also relies on this so it can
    pass ``tmp_path/app`` as the sole root without materialising a
    `scripts/` tree on every invocation.
    """
    extant = tmp_path / "extant"
    extant.mkdir()
    (extant / "hello.py").write_text("x = 1\n", encoding="utf-8")
    missing = tmp_path / "does_not_exist"

    pairs = list(_iter_containment_py_files((extant, missing)))

    # Only the extant root should contribute files; the missing root is
    # silently skipped rather than raising.
    assert pairs, "extant root must yield at least one py file"
    assert all(root == extant for root, _ in pairs), (
        f"missing root must be skipped, not yield anything: got roots {[r for r, _ in pairs]!r}"
    )

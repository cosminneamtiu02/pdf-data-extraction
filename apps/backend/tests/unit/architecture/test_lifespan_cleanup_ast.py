"""Architecture: every DI-cached `app.state` attribute must be cleanup-safe.

Issue #381: the old `_lifespan` cleanup hardcoded a 10-entry tuple of
`app.state` attribute names. Every new lazily-cached dependency added to
`app/api/deps.py` or `app/features/extraction/deps.py` had to be added to
the tuple — a footgun with no enforcement. The fix (same PR as this
test) switches the cleanup to a generic loop over the ``app.state``
attribute storage that cleans every attr not in the small
``_LIFESPAN_PRESERVED_ATTRS`` allowlist in ``app/main.py``. That change
covers every new lazily-cached attribute automatically.

This AST-scan is the belt-and-suspenders check. The runtime cleanup
will ``delattr`` any non-preserved attr — even ones without ``aclose``
— but a new DI-cached attribute that holds an open resource without
exposing ``aclose`` is still a leak. The scan enforces that every cached
attr is statically either async-closeable (exposes ``aclose`` on the
class body itself, NOT inherited) or explicitly allowlisted here as a
known-safe plain object. That way, if a future DI factory caches (say)
an ``httpx.AsyncClient`` directly on ``app.state`` without an
``aclose``-exposing wrapper, this test fails at scan time long before
production shutdown leaks the socket.

The scan walks the AST of the two DI factory modules and collects every
assignment of the shape ``state.X = <value>`` or ``app.state.X = <value>``
(where `state` was bound earlier in the same function to
`request.app.state`). For each such attribute, the right-hand side's
class name is looked up in the module's import table, and the resolved
class is required to either:

1. Expose an ``aclose`` method on the class body (``cls.__dict__``), OR
2. Be listed in ``_NON_CLOSEABLE_ALLOWLIST``.

Inherited ``aclose`` is deliberately NOT accepted — if ``aclose`` lives
on a base class, subclasses silently inherit it and the author of the
subclass may not even know the resource requires cleanup. Forcing the
``aclose`` declaration onto the concrete class makes the ownership
explicit and review-visible.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Final

import pytest

from ._linter_subprocess import BACKEND_DIR

_DEPS_FILES: Final[tuple[Path, ...]] = (
    BACKEND_DIR / "app" / "api" / "deps.py",
    BACKEND_DIR / "app" / "features" / "extraction" / "deps.py",
)

# Classes assigned onto `app.state.X` that are intentionally kept without
# `aclose()` because they are plain value objects with no network/file/thread
# handles. The keys are the attribute names (not class names) so a future
# rename of one of these classes surfaces as a "no entry" failure until the
# allowlist is updated intentionally.
#
# Semantics: allowlist entries do NOT leak resources when the cleanup loop's
# `delattr(app.state, attr)` runs — they are simple Python objects whose GC
# reclaims them normally.
#
# Intentional omissions from the allowlist:
# - ``settings`` and ``skill_manifest`` are set by ``create_app`` (before the
#   lifespan starts) and listed in ``app.main._LIFESPAN_PRESERVED_ATTRS``, so
#   the cleanup loop leaves them alone. They are also not assigned from within
#   the DI factories this scan walks, so adding them here would be dead code.
# - ``OllamaHealthProbe`` and ``OllamaGemmaProvider`` are async-closeable
#   and are detected automatically; they must NOT appear in this allowlist.
_NON_CLOSEABLE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        # `app.api.deps` — pipeline validator, parser, cached service holder.
        "structured_output_validator",
        "document_parser",
        "extraction_service",
        "probe_cache",
        # `app.features.extraction.deps` — pipeline collaborators.
        "text_concatenator",
        "extraction_engine",
        "span_resolver",
        "pdf_annotator",
    },
)


def _collect_imported_classes(tree: ast.AST) -> dict[str, str]:
    """Map a local name to its fully qualified ``module.attr``, for every ``from X import Y``.

    Returns ``{local_name: "module.path.ClassName"}``. Aliases are resolved
    (``from m import Cls as C`` yields ``{"C": "m.Cls"}``).

    Plain ``import pkg`` is intentionally NOT captured: the downstream
    resolver does not handle ``pkg.Cls(...)`` today — only unqualified
    ``Cls(...)`` calls and annotations. Capturing ``import pkg`` would
    populate ``{"pkg": "pkg"}``, which :func:`_resolve_class_has_aclose`
    would then try to ``importlib.import_module("") + getattr(mod, "pkg")``
    via ``rpartition(".")`` — a failing lookup that silently falls through
    to the allowlist check instead of flagging an unhandled shape. If a
    future factory starts using ``pkg.Cls(...)``, widen both this mapping
    (to capture ``import pkg``) and :func:`_resolve_rhs_class_name` (to
    handle ``ast.Attribute`` function refs) in the same change.
    """
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                local = alias.asname or alias.name
                mapping[local] = f"{node.module}.{alias.name}"
    return mapping


def _is_state_alias(target: ast.expr) -> bool:
    """Return True if ``target`` is the receiver of a ``state.X`` or ``app.state.X`` assignment.

    The DI factories use two equivalent shapes:
    - ``state = request.app.state`` → ``state.X = ...``
    - direct: ``request.app.state.X = ...`` (not used but covered for safety)
    """
    if isinstance(target, ast.Name) and target.id == "state":
        return True
    if isinstance(target, ast.Attribute) and target.attr == "state":
        inner = target.value
        return isinstance(inner, ast.Attribute) and inner.attr == "app"
    return False


def _collect_state_assignments(
    tree: ast.AST,
) -> dict[str, tuple[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]]:
    """Collect every ``state.X = <rhs>`` or ``app.state.X = <rhs>`` assignment.

    Returns ``{attr_name: (rhs_ast_node, enclosing_function_def)}``. The
    enclosing function is tracked so :func:`_resolve_rhs_class_name` can
    backtrack a ``Name`` RHS (``state.X = local_var``) to the variable's
    type annotation within the same function scope — the double-checked-
    locking DI pattern used by the real factories assigns the cached object
    via a local name rather than an inline constructor call.

    If the same attribute is assigned more than once in the module, the last
    assignment wins — fine because each factory only reassigns within the
    same critical section and to the same class.

    Implementation: a single recursive visitor that tracks the nearest
    enclosing FunctionDef (or AsyncFunctionDef) on a scope stack. An
    earlier revision ran ``ast.walk`` twice and used a per-parent ``scope``
    value derived from the parent type — that approach non-deterministically
    overwrote already-scoped entries with ``scope=None`` when the parent
    happened to be ``ast.Module``, because ``ast.walk`` traverses the tree
    in an unspecified order and can visit the Module node after the
    function bodies. The single-walk scope-stack visitor makes the mapping
    from ``attr_name`` to ``scope`` deterministic: it is always the
    NEAREST enclosing FunctionDef, which is exactly what the downstream
    ``_resolve_rhs_class_name(rhs, scope)`` expects.
    """
    assignments: dict[str, tuple[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]] = {}
    scope_stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def visit(node: ast.AST) -> None:
        pushed = False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope_stack.append(node)
            pushed = True
        if isinstance(node, ast.Assign):
            scope = scope_stack[-1] if scope_stack else None
            for target in node.targets:
                if not isinstance(target, ast.Attribute):
                    continue
                if _is_state_alias(target.value):
                    assignments[target.attr] = (node.value, scope)
        for child in ast.iter_child_nodes(node):
            visit(child)
        if pushed:
            scope_stack.pop()

    visit(tree)
    return assignments


def _is_none_constant(node: ast.AST) -> bool:
    """Return True iff ``node`` is the literal ``None`` constant."""
    return isinstance(node, ast.Constant) and node.value is None


def _first_non_none_class(nodes: tuple[ast.AST, ...]) -> str | None:
    """Return the first resolvable class name across ``nodes``, skipping ``None`` literals."""
    for node in nodes:
        if _is_none_constant(node):
            continue
        resolved = _extract_class_from_annotation(node)
        if resolved is not None:
            return resolved
    return None


def _extract_from_subscript(annotation: ast.Subscript) -> str | None:
    """Resolve ``Optional[X]`` or ``Union[X, None]`` to ``"X"``; else None."""
    if not isinstance(annotation.value, ast.Name):
        return None
    subscript_name = annotation.value.id
    if subscript_name == "Optional":
        return _extract_class_from_annotation(annotation.slice)
    if subscript_name == "Union" and isinstance(annotation.slice, ast.Tuple):
        return _first_non_none_class(tuple(annotation.slice.elts))
    return None


def _extract_class_from_annotation(annotation: ast.AST) -> str | None:
    """Extract the concrete class name from a type annotation AST.

    Handles the shapes that appear in the DI factories:

    - ``ClassName`` → ``"ClassName"``
    - ``ClassName | None`` → ``"ClassName"`` (the non-None BitOr arm)
    - ``Optional[ClassName]`` → ``"ClassName"``
    - ``Union[ClassName, None]`` → ``"ClassName"``

    Returns None for shapes the resolver does not understand.
    """
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _first_non_none_class((annotation.left, annotation.right))
    if isinstance(annotation, ast.Subscript):
        return _extract_from_subscript(annotation)
    return None


def _resolve_name_in_scope(name: str, scope: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Resolve a local variable name to a class name within a function scope.

    Preference order within ``scope``:

    1. An ``AnnAssign`` binding (``var: ClassName | None = ...``) — the DI
       factories always annotate the cached local, so this is the primary
       signal.
    2. An ``Assign`` whose RHS is a direct constructor call
       (``var = ClassName(...)``).

    Returns ``None`` if neither pattern is present.
    """
    for stmt in ast.walk(scope):
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == name
        ):
            resolved = _extract_class_from_annotation(stmt.annotation)
            if resolved is not None:
                return resolved
    for stmt in ast.walk(scope):
        if not isinstance(stmt, ast.Assign):
            continue
        for t in stmt.targets:
            if isinstance(t, ast.Name) and t.id == name and isinstance(stmt.value, ast.Call):
                resolved = _resolve_rhs_class_name(stmt.value, None)
                if resolved is not None:
                    return resolved
    return None


def _resolve_rhs_class_name(
    rhs: ast.AST,
    scope: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> str | None:
    """Return the constructor name for a class-instantiation RHS, else None.

    Examples:

    - ``ProbeCache(...)`` → ``"ProbeCache"``
    - ``OllamaGemmaProvider(settings=...)`` → ``"OllamaGemmaProvider"``
    - ``some_factory()`` (a Name call) → the name itself
    - ``state.X = provider`` where ``provider: OllamaGemmaProvider | None = ...``
      is annotated earlier in ``scope`` → ``"OllamaGemmaProvider"``

    Intentional NON-handling:

    - ``pkg.Cls(...)`` (an ``ast.Attribute`` callee) is NOT resolved today.
      A ``pkg.Cls`` callee would reach the ``isinstance(func, ast.Attribute)``
      branch below, which returns ``func.attr`` ("Cls") — but the import
      collector does not capture ``import pkg`` (see
      :func:`_collect_imported_classes` docstring), so the ``Cls`` name has
      no qualified path in the import table and the resolver falls through
      to the allowlist check. If a future factory needs that shape, both
      sides must widen together in a single change.

    Returns None for RHS shapes the resolver does not understand.
    """
    if isinstance(rhs, ast.Call):
        func = rhs.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None
    if isinstance(rhs, ast.Name) and scope is not None:
        return _resolve_name_in_scope(rhs.id, scope)
    return None


def _resolve_class_has_aclose(imports: dict[str, str], class_name: str) -> bool:
    """Return True iff the class identified by ``class_name`` declares ``aclose`` on its own body.

    Resolution strategy: look up ``class_name`` in the module's import table
    to find the module path, then import the module and ``getattr`` the
    class. Checking for ``aclose`` is done by inspecting ``cls.__dict__``
    so inherited ``aclose`` methods do NOT count as cleanup-safe.

    Why own-body only: if ``aclose`` lives on a base class, a subclass
    silently inherits it and the author of the subclass may not even know
    the resource requires cleanup. Forcing the ``aclose`` declaration onto
    the concrete class makes the ownership explicit and review-visible. A
    subclass that wants to accept the inherited behaviour must redeclare
    ``async def aclose(self): await super().aclose()`` — a one-liner that
    is easy to review and impossible to forget.

    Returns False if the class cannot be resolved (missing import entry,
    import failure, or attribute missing) — the caller will then fall
    through to the allowlist check.
    """
    qualified = imports.get(class_name)
    if qualified is None:
        return False
    module_path, _, attr = qualified.rpartition(".")
    if not module_path:
        return False
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return False
    cls = getattr(module, attr, None)
    if cls is None:
        return False
    # ``cls.__dict__`` contains ONLY members declared on this class, not
    # inherited members. A ``callable(getattr(cls, "aclose", None))`` check
    # would return True for any subclass of a closeable base, which is the
    # exact footgun this test exists to prevent.
    return callable(cls.__dict__.get("aclose"))


@pytest.mark.parametrize(
    "deps_file",
    _DEPS_FILES,
    ids=lambda p: str(p.relative_to(BACKEND_DIR)),
)
def test_every_cached_app_state_attribute_is_cleanup_safe(deps_file: Path) -> None:
    """Every ``state.X = Cls(...)`` in a DI factory must be cleanup-safe.

    Definition of cleanup-safe:
    - The assigned class exposes an ``aclose`` method on its own body (NOT
      inherited — see :func:`_resolve_class_has_aclose`), OR
    - The attribute name is explicitly listed in ``_NON_CLOSEABLE_ALLOWLIST``
      as a plain Python value object whose cleanup is a simple ``delattr``.

    A new DI-cached attribute that is neither async-closeable nor
    allowlisted will fail here with a clear message instead of silently
    leaking resources at shutdown time in production.
    """
    tree = ast.parse(deps_file.read_text(encoding="utf-8"), filename=str(deps_file))
    imports = _collect_imported_classes(tree)
    assignments = _collect_state_assignments(tree)

    offenders: list[str] = []
    for attr_name, (rhs, scope) in assignments.items():
        class_name = _resolve_rhs_class_name(rhs, scope)
        has_aclose = class_name is not None and _resolve_class_has_aclose(imports, class_name)
        allowlisted = attr_name in _NON_CLOSEABLE_ALLOWLIST
        if not has_aclose and not allowlisted:
            offenders.append(
                f"app.state.{attr_name} = {class_name}(...) — class has no "
                f"`aclose()` on its own body and attribute is not in the "
                f"non-closeable allowlist"
            )

    assert not offenders, (
        f"In {deps_file.relative_to(BACKEND_DIR)}: every DI-cached "
        "`app.state.X` attribute must either declare `aclose` on its own "
        "class body or be listed in `_NON_CLOSEABLE_ALLOWLIST` in this "
        "test file.\nOffenders:\n" + "\n".join(f"  - {o}" for o in offenders)
    )


def test_allowlist_is_not_stale() -> None:
    """Every allowlist entry must correspond to an actually-assigned attr.

    Prevents the allowlist from silently accumulating dead entries after
    an attribute is removed or renamed. Each entry must resolve to a
    `state.<name> = ...` assignment in one of the two DI factory files.
    """
    assigned_attrs: set[str] = set()
    for deps_file in _DEPS_FILES:
        tree = ast.parse(
            deps_file.read_text(encoding="utf-8"),
            filename=str(deps_file),
        )
        assigned_attrs.update(_collect_state_assignments(tree).keys())

    stale = _NON_CLOSEABLE_ALLOWLIST - assigned_attrs
    assert not stale, (
        "Entries in `_NON_CLOSEABLE_ALLOWLIST` that no longer correspond to "
        "a `state.X = ...` assignment in any DI factory. Either remove them "
        "from the allowlist or restore the assignment:\n"
        + "\n".join(f"  - {attr}" for attr in sorted(stale))
    )


def test_detector_collects_state_alias_assignments(tmp_path: Path) -> None:
    """Sanity check: the scanner finds ``state.X = ...`` after ``state = request.app.state``.

    Pins the AST collector behaviour so a future refactor of the detector
    cannot silently stop covering the real factory shape.
    """
    src = tmp_path / "mini_deps.py"
    src.write_text(
        "def factory(request):\n    state = request.app.state\n    state.my_dep = MyClass()\n",
        encoding="utf-8",
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    assignments = _collect_state_assignments(tree)
    assert "my_dep" in assignments


def test_detector_ignores_non_state_assignments(tmp_path: Path) -> None:
    """The scanner must not flag ``obj.x = ...`` where ``obj`` is unrelated to ``app.state``."""
    src = tmp_path / "mini_deps.py"
    src.write_text(
        "class Container:\n    def __init__(self):\n        self.value = 42\n",
        encoding="utf-8",
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    assignments = _collect_state_assignments(tree)
    assert assignments == {}


def test_detector_scope_is_deterministic_for_sync_functions(tmp_path: Path) -> None:
    """Scope resolution must always be the NEAREST enclosing FunctionDef.

    Pins the invariant that the single-walk scope-stack visitor replaced
    the older multi-walk ``ast.walk`` approach specifically to fix:
    the older version could (non-deterministically, due to ``ast.walk``
    ordering) record an assignment's ``scope`` as ``None`` after a later
    Module-rooted walk overwrote the correctly-scoped entry. If this test
    fails with ``assignment scope is None``, the determinism regression
    has returned and the downstream ``_resolve_name_in_scope`` call will
    silently fail to resolve annotations.
    """
    src = tmp_path / "mini_deps.py"
    src.write_text(
        "def factory(request):\n"
        "    state = request.app.state\n"
        "    dep: MyClass | None = None\n"
        "    dep = MyClass()\n"
        "    state.my_dep = dep\n",
        encoding="utf-8",
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    assignments = _collect_state_assignments(tree)
    assert "my_dep" in assignments
    _rhs, scope = assignments["my_dep"]
    assert scope is not None, "assignment scope must be the enclosing FunctionDef, not None"
    assert isinstance(scope, ast.FunctionDef)
    assert scope.name == "factory"


def test_detector_scope_is_deterministic_for_async_functions(tmp_path: Path) -> None:
    """Same determinism contract as the sync test, but for ``async def`` factories.

    The scope stack treats ``ast.AsyncFunctionDef`` and ``ast.FunctionDef``
    identically; this pins the shape so a future refactor of the visitor
    cannot silently drop async-function support.
    """
    src = tmp_path / "mini_deps.py"
    src.write_text(
        "async def factory(request):\n"
        "    state = request.app.state\n"
        "    dep: MyClass | None = None\n"
        "    dep = MyClass()\n"
        "    state.my_dep = dep\n",
        encoding="utf-8",
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    assignments = _collect_state_assignments(tree)
    assert "my_dep" in assignments
    _rhs, scope = assignments["my_dep"]
    assert scope is not None
    assert isinstance(scope, ast.AsyncFunctionDef)
    assert scope.name == "factory"


def test_resolve_class_has_aclose_rejects_inherited_aclose() -> None:
    """Inherited ``aclose`` must NOT count as cleanup-safe.

    Pins the ``cls.__dict__`` containment check: a subclass that inherits
    ``aclose`` from a base class is treated as non-closeable so authors
    cannot accidentally rely on invisible-at-the-class-body cleanup
    behaviour. Subclasses that legitimately want inherited cleanup must
    redeclare ``aclose`` (e.g. ``async def aclose(self): await super().aclose()``).
    """

    class _Base:
        async def aclose(self) -> None:  # pragma: no cover - introspection target
            return

    class _DerivedInherits(_Base):
        """Inherits ``aclose`` but does not redeclare it."""

    class _DerivedRedeclares(_Base):
        async def aclose(self) -> None:  # pragma: no cover - introspection target
            await super().aclose()

    # The production code resolves classes through an import table. Simulate
    # that by injecting synthetic imports and a synthetic module.
    import sys
    import types

    module = types.ModuleType("test_dynamic_aclose_module")
    module.BaseCls = _Base  # type: ignore[attr-defined]
    module.Inherited = _DerivedInherits  # type: ignore[attr-defined]
    module.Redeclared = _DerivedRedeclares  # type: ignore[attr-defined]
    sys.modules[module.__name__] = module
    try:
        imports = {
            "BaseCls": f"{module.__name__}.BaseCls",
            "Inherited": f"{module.__name__}.Inherited",
            "Redeclared": f"{module.__name__}.Redeclared",
        }
        # Base: has aclose on own body.
        assert _resolve_class_has_aclose(imports, "BaseCls") is True
        # Inherited: DOES NOT count — aclose is only on the base.
        assert _resolve_class_has_aclose(imports, "Inherited") is False
        # Redeclared: counts — aclose is on the subclass body too.
        assert _resolve_class_has_aclose(imports, "Redeclared") is True
    finally:
        del sys.modules[module.__name__]
